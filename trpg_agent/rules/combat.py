"""Pure combat arithmetic pulled out of the dice cog (golden rule #2: dice rolling AND
resolution are deterministic engine/rules code, never the LLM). No Discord, no WorldState
mutation — the RNG is injected and every soak/roll/Perils computation is reproducible from a
seed. The cog keeps the IO and the state mutations (``apply_damage`` / ``reset_warp_charge``)
and the post-mutation narration (``engine.describe_damage_de``, which reads updated wounds).

Objects (profile / store / target / character) are duck-typed — the cog passes its live
WorldState combatant, PC sheet and CharacterStore through unchanged.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from . import engine
from .characters import augmetic_armour


def toughness_bonus(profile, store, character) -> int:
    """Toughness Bonus for a player from the sheet: the profile's soak characteristic (IM: Tgh),
    rendered per soak mode (IM: tens digit). 0 if no profile/character/characteristic."""
    if profile is None or character is None:
        return 0
    char_key = profile.soak_characteristic()
    if not char_key:
        return 0
    # Reuse the store's lookup (case-insensitive + strip, skill→characteristic fallback) so a
    # whitespace-drifted sheet key (e.g. "Tgh ") still resolves (finding #9).
    value = store.skill_value(character, char_key) if store else None
    if value is None:
        return 0
    return value // 10 if profile.soak_mode() == "tens" else value


@dataclass(frozen=True, slots=True)
class AttackOutcome:
    """The deterministic result of one attack's soak + weapon-damage resolution."""

    toughness_bonus: int
    armour: int
    augmetic_armour: int
    soak: int
    damage: engine.DamageResult


def resolve_attack(
    profile, store, *, target, target_sheet, notation, success_level, rng: random.Random
) -> AttackOutcome:
    """Roll the weapon's damage and subtract the target's soak (Toughness Bonus + armour, plus
    augmetic armour for a PC, ADR 023), yielding the wounds applied. Pure: rolls through the
    injected RNG, mutates nothing. The cog applies ``damage.applied`` to the WorldState and
    narrates the consequence afterward.

    ``target`` is the WorldState combatant (duck-typed: ``.is_npc``, ``.toughness_bonus``,
    ``.armour``); ``target_sheet`` is the PC ``Character`` sheet, or ``None`` for an NPC."""
    augm_armour = 0
    if target.is_npc:
        tb = target.toughness_bonus
    else:
        tb = toughness_bonus(profile, store, target_sheet)
        if profile is not None:  # augmetic armour adds to a PC's soak (ADR 023)
            augm_armour = augmetic_armour(profile, target_sheet)
    soak = tb + target.armour + augm_armour
    weapon_roll = engine.roll_damage(notation, rng)
    dmg = engine.resolve_damage(weapon_roll, success_level, soak)
    return AttackOutcome(
        toughness_bonus=tb, armour=target.armour, augmetic_armour=augm_armour,
        soak=soak, damage=dmg,
    )


@dataclass
class WarpConsequence:
    """The Perils/containment lines a manifest produced, plus whether Warp Charge must reset.
    The cog applies ``reset_charge`` via ``state.reset_warp_charge`` — this stays pure."""

    lines: list[str]
    reset_charge: bool


def resolve_warp_consequences(
    profile, *, immediate_perils, over_threshold, warp_charge, threshold, contain_base,
    character, rng: random.Random,
) -> WarpConsequence:
    """Resolve the Perils-of-the-Warp risk a manifest just created (ADR 022). A Push-Fumble
    triggers Perils immediately (IM p.163); otherwise, if Warp Charge now exceeds the Threshold,
    the psyker makes the Challenging containment Test — on success the energy is held (powers
    turn Overt), on failure Perils erupt. Pure: the cog applies ``reset_charge`` (Perils resets
    Warp Charge to 0 and ends Sustained powers)."""
    if not (immediate_perils or over_threshold):
        return WarpConsequence(lines=[], reset_charge=False)
    over_by = max(0, warp_charge - threshold)
    lines: list[str] = []
    if not immediate_perils:  # over threshold → containment Test first
        # The containment Test rolls against Disziplin (Psi), not Psi-Meisterschaft (IM p.163).
        contain_target = (contain_base or 0) + (profile.difficulty_modifier("Herausfordernd") or 0)
        contain = engine.resolve_test(profile, contain_target, rng)
        lines.append(engine.describe_result_de(
            contain, skill="Warp-Kontrolle", character=character, difficulty="Herausfordernd"))
        if contain.success:
            lines.append(f"🜏 {character} hält die Warp-Energie zurück — alle gewirkten Kräfte gelten "
                         "bis zur Beruhigung als offen (Overt).")
            return WarpConsequence(lines=lines, reset_charge=False)
    perils = engine.resolve_perils(profile, over_by=over_by, rng=rng)
    lines.append(engine.describe_perils_de(perils, character=character))
    if perils.effect:
        lines.append(f"   → {perils.effect}")
    return WarpConsequence(lines=lines, reset_charge=True)
