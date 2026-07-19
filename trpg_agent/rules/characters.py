"""Character store — lean structured JSON the engine rolls against (ADR 004 + D12).

Characters live as ``data/sessions/<channel>/characters.json``: the player sheets transferred
**once** into JSON (sheets never go into RAG). The *shape* follows the active system profile
(§9). This gives two things Phase 8 needs:

1. **Stat-aware target resolution** — the GM rolls *for* the player: target = skill value
   (from here) ± difficulty modifier (from the profile ladder). Neither number comes from the
   LLM (golden rule #2 / open item K).
2. **Display-name → character alias map** — fixes the model confusing "SezBoss69" with the
   character "Seskin" (open item F); injected as a light hint into the prompt.

Pure data + pure functions, unit-tested without Discord or the LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .profile import SystemProfile


@dataclass(frozen=True, slots=True)
class Character:
    """One player character. Stat fields follow the active profile's schema."""

    name: str
    characteristics: dict[str, int] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    wounds: int | None = None
    max_wounds: int | None = None
    inventory: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    # Psyker fields (ADR 022) — optional; a non-psyker leaves these falsy. ``known_powers`` are
    # names that index the active profile's psyker catalog (the stat block lives there, not here).
    psyker: bool = False
    disciplines: tuple[str, ...] = ()
    known_powers: tuple[str, ...] = ()
    # Augmetics (ADR 023) — names that index the active profile's augmetics catalog (the effects
    # live there, not here). Passive: no roll; the engine reads their armour/characteristic effects.
    augmetics: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Character":
        return cls(
            name=data["name"],
            characteristics={k: int(v) for k, v in (data.get("characteristics", {}) or {}).items()},
            skills={k: int(v) for k, v in (data.get("skills", {}) or {}).items()},
            wounds=data.get("wounds"),
            max_wounds=data.get("max_wounds"),
            inventory=tuple(data.get("inventory", []) or []),
            conditions=tuple(data.get("conditions", []) or []),
            psyker=bool(data.get("psyker", False)),
            disciplines=tuple(data.get("disciplines", []) or []),
            known_powers=tuple(data.get("known_powers", []) or []),
            augmetics=tuple(data.get("augmetics", []) or []),
            raw=dict(data),
        )


@dataclass(frozen=True, slots=True)
class ResolvedTest:
    """A test request resolved against a character + profile, ready for the engine.

    ``target`` is ``None`` when the skill value is unknown (no character/skill match) — the
    caller still rolls the die but can't compute SL, so it asks the player to compare manually.
    """

    skill: str
    character: Character | None
    base: int | None              # the skill (or characteristic) value, before difficulty
    modifier: int                 # the difficulty modifier applied
    difficulty: str | None        # canonical difficulty label, for display
    target: int | None            # base + modifier (None if base is None)


class CharacterStore:
    """Characters + a display-name→character alias map for one session."""

    def __init__(
        self, characters: list[Character] | None = None, aliases: dict[str, str] | None = None
    ) -> None:
        self._by_name: dict[str, Character] = {c.name.lower(): c for c in (characters or [])}
        # alias (display name) → character name, lower-cased keys for case-insensitive lookup …
        self._aliases: dict[str, str] = {k.lower(): v for k, v in (aliases or {}).items()}
        # … but keep the original-case display names for the prompt hint.
        self._alias_pairs: list[tuple[str, str]] = list((aliases or {}).items())

    # -- construction ---------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterStore":
        chars = [Character.from_dict(c) for c in data.get("characters", [])]
        return cls(chars, data.get("aliases", {}))

    @classmethod
    def load(cls, path: Path) -> "CharacterStore":
        """Load a characters JSON. A missing file yields an empty store (no characters yet)."""
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # -- lookup ---------------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_name)

    def characters(self) -> list[Character]:
        """All loaded characters (insertion order). Used to seed the mutable world state (§7)."""
        return list(self._by_name.values())

    def get(self, name: str | None) -> Character | None:
        """Find a character by character name or by a player's display-name alias."""
        if not name:
            return None
        key = name.lower()
        if key in self._by_name:
            return self._by_name[key]
        alias = self._aliases.get(key)
        if alias and alias.lower() in self._by_name:
            return self._by_name[alias.lower()]
        return None

    def skill_value(self, character: Character | None, skill: str) -> int | None:
        """Skill value for ``skill`` (case-insensitive). Falls back to a same-named
        characteristic (the IM governing characteristic is often what a 'test' names), else
        ``None``."""
        if character is None or not skill:
            return None
        key = skill.strip().lower()
        for name, value in character.skills.items():
            if name.strip().lower() == key:
                return value
        for name, value in character.characteristics.items():
            if name.strip().lower() == key:
                return value
        return None

    def alias_hint_de(self) -> str:
        """The 'who plays whom' block appended **last** to the system prompt (open item F). Besides
        mapping display name → character (so the model stops confusing "SezBoss69" with "Seskin"),
        it draws the hard boundary that these characters belong to the players. Placed last so it is
        the *freshest* instruction the model reads — the live fix for nemo puppeting the whole party
        (it kept speaking and acting for every PC, players: "hat noch nicht gerafft, dass es mehrere
        Spieler gibt"). Empty if no aliases."""
        if not self._alias_pairs:
            return ""
        pairs = "; ".join(f"{display} spielt {char}" for display, char in self._alias_pairs)
        chars = ", ".join(dict.fromkeys(char for _, char in self._alias_pairs))  # dedupe, keep order
        return (
            f"Am Tisch sitzen mehrere Spielende mit je einer eigenen Figur: {pairs}. "
            f"Diese Spielfiguren ({chars}) gehören allein den Spielenden — du sprichst, denkst und "
            "handelst NIE für sie und erfindest weder ihre Worte noch ihre Taten. Du steuerst "
            "ausschließlich NSCs, Gegner und die Umgebung und reagierst nur auf das, was die "
            "Spielenden wirklich sagen oder tun."
        )

    def intro_roster_de(self) -> str:
        """A compact German party roster for the one-time ``!intro`` opening monologue (ADR 031):
        one bullet per character with the flavour the DM needs to give each figure a personal beat —
        concept, then (full depth, Tobi's choice) origin, faction, distinguishing marks, goals,
        connections and character arc. Read from each :class:`Character`'s ``raw`` source dict and
        tolerant of missing fields (the lean ``_example`` sheets carry only some), so it degrades
        gracefully; ``""`` when no characters are loaded. Embedded into the ``!intro`` director
        instruction — it rides in the turn's user message, so the ADR-019 prompt order is untouched.
        Never read aloud verbatim: the director tells the model to weave it in and only hint at
        private goals."""
        if not self._by_name:
            return ""
        # (raw field, German label) appended after the concept lead descriptor, each present field as
        # "Label: value"; whitespace in multi-line sheet fields (goals/arc) is collapsed to one line.
        fields = [
            ("origin", "Herkunft"),
            ("faction", "Fraktion"),
            ("distinguishing", "Auffällig"),
            ("goals", "Ziele"),
            ("connections", "Verbindungen"),
            ("arc", "Wandel"),
        ]
        lines: list[str] = []
        for char in self._by_name.values():
            segs: list[str] = []
            concept = " ".join(str(char.raw.get("concept", "")).split())
            if concept:
                segs.append(concept)  # lead descriptor (no label)
            for key, label in fields:
                value = " ".join(str(char.raw.get(key, "")).split())
                if value:
                    segs.append(f"{label}: {value}")
            body = "; ".join(segs)
            lines.append(f"- **{char.name}**" + (f" — {body}" if body else ""))
        return "\n".join(lines)

    def character_names(self) -> list[str]:
        """The player-character names (no alias display names), in load order — used by the intro
        guard to check the opening monologue actually weaves in every figure (ADR 041 follow-up)."""
        return [c.name for c in self._by_name.values() if c.name]

    def speaker_labels(self) -> list[str]:
        """Every name that may prefix a *scripted* line the model must not write — each character
        name plus each player display name. The orchestrator adds these (beside the turn's own
        speakers) as cut-labels + stop sequences, so a puppeted ``Seskin: …`` / ``Pr0degie: …``
        script the model tacks on gets chopped off post-hoc: the deterministic backstop to the
        persona's "never speak or act for the player characters" rule (nemo ignores the soft rule
        and scripts the whole party — the live puppeting + runaway-length fix)."""
        names = [c.name for c in self._by_name.values()]
        names += [display for display, _ in self._alias_pairs]
        return list(dict.fromkeys(n for n in names if n))  # dedupe, keep order


def augmetic_armour(profile: SystemProfile, character: Character | None) -> int:
    """Total armour an augmetic character adds to soak (ADR 023): sum of every ``armour`` effect
    across the character's catalogued augmetics. 0 for a non-augmetic character. Engine-applied
    because dice/soak = code (golden rule #2)."""
    if character is None or not character.augmetics or not profile.augmetics_enabled():
        return 0
    total = 0
    for name in character.augmetics:
        stats = profile.augmetic(name)
        if stats is None:
            continue
        for eff in stats.get("effects", []) or []:
            if eff.get("type") == "armour":
                total += int(eff.get("value", 0) or 0)
    return total


def augmetic_bonus(profile: SystemProfile, character: Character | None, *, characteristic: str) -> int:
    """The augmetic bonus to a test on ``characteristic`` (ADR 023): sum of every ``characteristic``
    effect whose boosted characteristic matches by name, or whose optional ``skills`` list contains
    ``characteristic`` (so e.g. an Augur-Array's +5 Perception lifts Wahrnehmung Tests). Skills not
    listed there aren't auto-boosted (governance isn't modelled) — the DM applies those from the
    prompt block. 0 when nothing matches."""
    if character is None or not character.augmetics or not profile.augmetics_enabled() or not characteristic:
        return 0
    key = characteristic.strip().lower()
    total = 0
    for name in character.augmetics:
        stats = profile.augmetic(name)
        if stats is None:
            continue
        for eff in stats.get("effects", []) or []:
            if eff.get("type") != "characteristic":
                continue
            names = [str(eff.get("characteristic", "")).lower()]
            names += [str(s).lower() for s in (eff.get("skills", []) or [])]
            if key in names:
                total += int(eff.get("value", 0) or 0)
    return total


def resolve_target(
    profile: SystemProfile,
    store: CharacterStore | None,
    *,
    skill: str,
    target_name: str | None = None,
    difficulty: str | None = None,
    modifier: int | None = None,
) -> ResolvedTest:
    """Resolve a parsed test into a numeric target — the 'dice = code' core (open item K).

    target = skill value (from the character JSON) + difficulty modifier (explicit ``±N``
    override, else the profile difficulty ladder). The LLM supplies neither number.
    """
    character = store.get(target_name) if store is not None else None
    base = store.skill_value(character, skill) if store is not None else None
    if base is not None:  # augmetic characteristic bonuses (e.g. Augur-Array +5 Per) lift the value
        base += augmetic_bonus(profile, character, characteristic=skill)

    if modifier is not None:
        mod, label = modifier, None  # explicit ±N override from the marker
    else:
        mod = profile.difficulty_modifier(difficulty)
        if mod is None:  # unknown word → fall back to the profile default
            mod = profile.difficulty_modifier(None) or 0
            label = profile.canonical_difficulty(None)
        else:
            label = profile.canonical_difficulty(difficulty)

    target = base + mod if base is not None else None
    return ResolvedTest(
        skill=skill, character=character, base=base, modifier=mod, difficulty=label, target=target
    )


@dataclass(frozen=True, slots=True)
class ResolvedManifest:
    """A Manifest request resolved against a character + profile, ready for the engine (ADR 022).

    ``target`` is ``None`` when the Psi-Meisterschaft value is unknown (no character or the
    psyker skill isn't on the sheet) — the caller still rolls but can't compute SL/Warp Charge."""

    power: str                    # the power name (canonical, from the catalog)
    character: Character | None
    stats: dict | None            # the power's catalog stat block (warp_rating, difficulty, …)
    warp_rating: int              # the power's base Warp Rating
    base: int | None              # the Psi-Meisterschaft skill value, before difficulty
    contain_base: int | None      # the Disziplin (Psi) value — drives the Warp-containment Test, not Manifest
    modifier: int                 # the power's Difficulty modifier
    difficulty: str | None        # canonical difficulty label, for display
    target: int | None            # base + modifier (None if base is None)
    willpower_bonus: int          # tens of Willpower (drives Warp Charge gain on a Critical)
    threshold: int                # Warp Threshold (= Willpower Bonus in IM)


def resolve_manifest_request(
    profile: SystemProfile,
    store: CharacterStore | None,
    *,
    power: str,
    target_name: str | None = None,
) -> ResolvedManifest | None:
    """Resolve a parsed Manifest request into the numbers :func:`engine.resolve_manifest` needs.

    Looks the power up in the profile catalog (its Warp Rating + Difficulty), reads the psyker's
    Psi-Meisterschaft value and Willpower from the sheet, and computes the test target + Warp
    Threshold — all in code (golden rule #2). Returns ``None`` if the profile has no psyker block
    or the power isn't catalogued, so the caller can fall back to a plain narration."""
    if not profile.psyker_enabled():
        return None
    stats = profile.power(power)
    if stats is None:
        return None
    character = store.get(target_name) if store is not None else None
    base = store.skill_value(character, profile.psyker_test_skill()) if store is not None else None
    # The containment ("do Perils erupt") Test rolls against Disziplin (Psi), NOT Psi-Meisterschaft
    # (IM p.163) — a separate skill, so it gets its own base (None-handled like ``base``).
    contain_base = store.skill_value(character, profile.psyker_purge_skill()) if store is not None else None
    mod = profile.difficulty_modifier(stats.get("difficulty")) or 0
    label = profile.canonical_difficulty(stats.get("difficulty"))
    wil_value = store.skill_value(character, profile.threshold_characteristic()) if store else None
    wb = (wil_value // 10) if wil_value is not None else 0
    return ResolvedManifest(
        power=stats["name"], character=character, stats=stats,
        warp_rating=int(stats.get("warp_rating", 0) or 0),
        base=base, contain_base=contain_base, modifier=mod, difficulty=label,
        target=(base + mod) if base is not None else None,
        willpower_bonus=wb, threshold=profile.warp_threshold(wil_value),
    )
