"""Generic dice + resolution engine (ADR 005) — the deterministic heart of the project.

Golden rule #2: dice (RNG) **and** their resolution (success, degrees, crit, damage) are
computed here, never by the LLM. The engine is system-agnostic: it takes a numeric target
and a :class:`~dmbot.rules.profile.SystemProfile` and applies the profile's resolution kind.
Imperium Maledictum (1d100 roll-under, SL = tens-difference) is the first profile; other
systems are other profiles plugged into ``RESOLVERS``.

Everything is pure and takes an explicit ``rng: random.Random`` (default a module-level
``Random``), so tests seed it and assert exact outcomes. The cog resolves the *target*
(skill value + difficulty modifier) before calling in — the engine never reads characters.
"""

from __future__ import annotations

import inspect
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from .profile import SystemProfile

_DICE_RE = re.compile(r"^\s*(\d*)\s*[dD]\s*(\d+)\s*([+-]\s*\d+)?\s*$")
_INT_RE = re.compile(r"^\s*([+-]?\d+)\s*$")

_default_rng = random.Random()


class DiceError(ValueError):
    """Unparseable dice notation."""


@dataclass(frozen=True, slots=True)
class DiceRoll:
    """The outcome of a dice expression like ``2d10+3``."""

    total: int
    dice: tuple[int, ...]
    modifier: int
    notation: str


def roll(notation: str, rng: random.Random | None = None) -> DiceRoll:
    """Roll a dice expression: ``XdY``, ``dY``, with an optional ``+N``/``-N`` modifier, or a
    bare integer constant. ``1d5`` is a flat 1–5 die (distribution-equal to ceil(d10/2))."""
    rng = rng or _default_rng
    m = _DICE_RE.match(notation)
    if m:
        count = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        modifier = int(m.group(3).replace(" ", "")) if m.group(3) else 0
        if count < 1 or sides < 1:
            raise DiceError(f"invalid dice notation: {notation!r}")
        dice = tuple(rng.randint(1, sides) for _ in range(count))
        return DiceRoll(total=sum(dice) + modifier, dice=dice, modifier=modifier, notation=notation)
    m = _INT_RE.match(notation)
    if m:  # a constant (e.g. damage "+2" or a fixed value)
        value = int(m.group(1))
        return DiceRoll(total=value, dice=(), modifier=value, notation=notation)
    raise DiceError(f"unparseable dice notation: {notation!r}")


def roll_damage(notation: str, rng: random.Random | None = None) -> DiceRoll:
    """Roll a damage expression (same parser as :func:`roll`; named for call-site clarity)."""
    return roll(notation, rng)


@dataclass(frozen=True, slots=True)
class TestResult:
    """The resolved outcome of a skill test under a profile."""

    roll: int           # the d100 face, 1..100 (100 is the percentile "00")
    target: int         # the effective target (skill value ± difficulty)
    success: bool
    degrees: int        # success levels (SL): + on success, − on failure (tens-difference)
    critical: bool      # a successful double (11, 22, … 00) — a critical success
    fumble: bool        # a failed double — a fumble
    auto: bool          # decided by the auto-success/auto-fail band, overriding the comparison
    resolution: str     # the profile resolution kind that produced this


def _tens(n: int) -> int:
    """Tens digit for SL: 1..99 → 0..9, 100 → 10 (percentile "00")."""
    return n // 10


def _is_double(face: int) -> bool:
    """d100 doubles: 11, 22, … 99, and 100 (the "00" double)."""
    return face == 100 or (1 <= face <= 99 and face % 11 == 0)


def reverse_d100(face: int) -> int:
    """Swap a d100 face's tens and units dice (IM Advantage/Disadvantage, Core Rulebook p.189):
    72→27, 05→50, 40→04. The percentile '00' (100) reverses to itself."""
    d = face % 100  # 100 ("00") → 0
    rev = (d % 10) * 10 + (d // 10)
    return rev if rev != 0 else 100


def resolve_roll_under(
    profile: SystemProfile, target: int, rng: random.Random | None = None, *, advantage: int = 0
) -> TestResult:
    """1d100 roll-under (IM): success if roll ≤ target; SL = tens(target) − tens(roll);
    a double on a success is a critical, on a failure a fumble; the 01–05 / 96–00 bands
    force success/failure regardless of the target.

    ``advantage`` models IM Advantage/Disadvantage (p.189): a single net Advantage (+1) lets the
    roll's tens/units be reversed when that helps (lower is better here); a single Disadvantage
    (−1) forces the reversal when it hurts. Each *additional* source beyond the first is a flat
    ±10 to the target (p.189). 0 (the default) leaves the original behaviour untouched — Push is
    the only current caller (+1)."""
    rng = rng or _default_rng
    if advantage:  # extra sources past the first are ±10 to the effective target
        target += (abs(advantage) - 1) * 10 * (1 if advantage > 0 else -1)
    face = rng.randint(1, 100)
    if advantage:
        rev = reverse_d100(face)
        face = min(face, rev) if advantage > 0 else max(face, rev)
    success = face <= target
    auto = False
    if profile.auto_success_max and face <= profile.auto_success_max:
        success, auto = True, True
    elif profile.auto_fail_min and face >= profile.auto_fail_min:
        success, auto = False, True
    # Auto-band results are a "Marginal Success/Failure": SL 0, and no crit/fumble (IM p.188).
    degrees = 0 if auto else (_tens(target) - _tens(face) if profile.degrees == "tens_difference" else 0)
    double = not auto and profile.crit == "doubles" and _is_double(face)
    return TestResult(
        roll=face, target=target, success=success, degrees=degrees,
        critical=double and success, fumble=double and not success,
        auto=auto, resolution="roll_under",
    )


# Resolution registry — other systems (roll-over vs DC, pools, sum_vs_target) plug in here.
RESOLVERS = {
    "roll_under": resolve_roll_under,
}


@lru_cache(maxsize=None)
def _accepts_advantage(resolver: Callable) -> bool:
    """Does ``resolver`` accept an ``advantage`` keyword (named param or ``**kwargs``)?

    Decided by signature, never by catching the call — so a genuine ``TypeError`` raised
    *inside* a resolver propagates instead of being masked by a silent re-roll (golden rule #2).
    Cached: the resolver set is tiny and fixed at registration time."""
    try:
        params = inspect.signature(resolver).parameters
    except (TypeError, ValueError):  # builtins / C funcs without an inspectable signature
        return False
    if "advantage" in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def resolve_test(
    profile: SystemProfile, target: int, rng: random.Random | None = None, *, advantage: int = 0
) -> TestResult:
    """Roll and resolve a test under ``profile`` against the already-resolved ``target``.

    ``advantage`` (net Advantage − Disadvantage levels) is passed to the resolver only if its
    signature accepts it; resolvers that don't model it are called without the keyword. Any error
    raised inside the resolver propagates — there is no retry that could mask a bug or consume a
    second d100."""
    resolver = RESOLVERS.get(profile.resolution)
    if resolver is None:
        raise NotImplementedError(
            f"resolution {profile.resolution!r} is not implemented yet "
            f"(known: {', '.join(sorted(RESOLVERS))})"
        )
    if _accepts_advantage(resolver):
        return resolver(profile, target, rng, advantage=advantage)
    return resolver(profile, target, rng)


def _face_str(face: int) -> str:
    """Show the d100 face the table way: 100 as '00'."""
    return "00" if face == 100 else f"{face:02d}"


def describe_result_de(
    result: TestResult, *, skill: str, character: str | None = None, difficulty: str | None = None
) -> str:
    """A German one-line summary, in the GM-rolls-for-the-player style the table asked for
    (open item K): "🎲 Tobi auf Wahrnehmung (Ziel 35): 23 — Erfolg, 1 EG." Feeds back into the
    DM context so the model narrates the consequence."""
    who = character or "Wurf"
    diff = f", {difficulty}" if difficulty else ""
    head = f"🎲 {who} auf {skill}{diff} (Ziel {result.target}): {_face_str(result.roll)}"
    if result.critical:
        verdict = f"kritischer Erfolg, {result.degrees} EG"
    elif result.fumble:
        verdict = f"Patzer, {abs(result.degrees)} EG Fehlschlag"
    elif result.success:
        verdict = f"Erfolg, {result.degrees} EG"
    else:
        verdict = f"Fehlschlag, {abs(result.degrees)} EG"
    if result.auto:
        verdict += " (automatisch)"
    return f"{head} — {verdict}."


@dataclass(frozen=True, slots=True)
class DamageResult:
    """An attack's damage, applied to a target's wounds. Pure: the RNG already happened in
    :func:`roll_damage`; this is the deterministic arithmetic that turns it into wounds lost."""

    weapon_roll: DiceRoll   # the weapon's damage dice (e.g. 1d10+5 → 8)
    success_level: int      # SL of the attack test (degrees), added to damage in IM
    soak: int               # Toughness Bonus + armour, subtracted
    applied: int            # wounds actually lost (never < 0)


def resolve_damage(weapon_roll: DiceRoll, success_level: int, soak: int) -> DamageResult:
    """IM 'Dealing Damage' (Core Rulebook p.214): wounds lost = weapon Damage + SL, reduced by soak
    (Toughness Bonus + armour), never below 0. SL is only added when positive (an attack only deals
    damage on a success, so SL ≥ 0, but guard regardless). System-agnostic in shape — another
    profile can soak differently; the cog supplies the numbers from the active profile."""
    applied = max(0, weapon_roll.total + max(0, success_level) - max(0, soak))
    return DamageResult(
        weapon_roll=weapon_roll, success_level=success_level, soak=soak, applied=applied
    )


def describe_damage_de(
    dmg: DamageResult,
    *,
    attacker: str | None,
    target: str,
    weapon: str | None,
    new_wounds: int,
    max_wounds: int,
    downed: bool,
) -> str:
    """A German one-line damage summary that feeds back so the DM narrates the consequence:
    "💥 Vask trifft Kultist mit Kettenschwert: 8 + 2 EG − 3 Soak = 7 Wunden → Kultist 3/10."
    """
    who = attacker or "Angreifer"
    weap = f" mit {weapon}" if weapon else ""
    sl = f" + {dmg.success_level} EG" if dmg.success_level else ""
    soak = f" − {dmg.soak} Soak" if dmg.soak else ""
    state = "kampfunfähig" if downed else f"{new_wounds}/{max_wounds}"
    return (
        f"💥 {who} trifft {target}{weap}: {dmg.weapon_roll.total}{sl}{soak} "
        f"= {dmg.applied} Wunden → {target} {state}."
    )


# =====================================================================================
# Psyker / Warp (IM Core Rulebook ch. VI "Psychic Powers", p.158–165) — ADR 022.
#
# Still "dice = code, narration = LLM" (golden rule #2) and still system-agnostic: every
# number here comes from the profile's ``psyker`` block (powers' Warp Rating + Difficulty,
# the Warp Threshold formula, the Perils/Phenomena tables). A profile with no ``psyker``
# block simply has no Warp flow. The Manifest Test reuses :func:`resolve_test`; what this
# section adds is the Warp-Charge bookkeeping and the two consequence tables.
# =====================================================================================


@dataclass(frozen=True, slots=True)
class ManifestResult:
    """The resolved outcome of a Manifest Test (manifesting one psychic power)."""

    test: TestResult        # the underlying Psychic Mastery test
    power: str              # the power's name (as keyed in the profile catalog)
    warp_rating: int        # the power's Warp Rating after any Critical reduction
    charge_gained: int      # Warp Charge gained by this manifest (after Fumble/Push effects)
    warp_charge: int        # the psyker's new total Warp Charge
    threshold: int          # Warp Threshold (IM: Willpower Bonus)
    over_threshold: bool    # charge > threshold → Perils risk at end of the psyker's turn
    pushed: bool            # the psyker Pushed (Advantage + extra charge)
    immediate_perils: bool  # Push + Fumble → Perils triggers immediately (p.163)


def warp_charge_gain(
    test: TestResult, warp_rating: int, willpower_bonus: int, *, pushed: bool = False, push_roll: int = 0
) -> int:
    """Warp Charge gained by a Manifest Test (IM p.163, "Gain Warp Charge"):

    - **Success:** gain the power's Warp Rating. A **Critical** reduces that rating by the
      psyker's Willpower Bonus first (min 1).
    - **Failure:** gain 1 per −SL, capped at the Warp Rating.
    - **Fumble:** double the amount gained.
    - **Push:** add ``push_roll`` (1d10) on top, regardless of success (rolled by the caller).
    """
    if test.success:
        rating = max(1, warp_rating - willpower_bonus) if test.critical else warp_rating
        gain = rating
    else:
        gain = min(warp_rating, abs(test.degrees))
    if test.fumble:
        gain *= 2
    if pushed:
        gain += max(0, push_roll)
    return gain


def resolve_manifest(
    profile: SystemProfile,
    *,
    test_target: int,
    power: str,
    warp_rating: int,
    current_charge: int,
    willpower_bonus: int,
    threshold: int | None = None,
    pushed: bool = False,
    rng: random.Random | None = None,
) -> ManifestResult:
    """Manifest a psychic power: roll the Psychic Mastery Test against ``test_target`` (already
    resolved = skill value ± the power's Difficulty), then bookkeep Warp Charge per IM p.163.

    ``warp_rating`` and the power's Difficulty come from the profile catalog; the caller resolves
    them into ``test_target`` exactly like a normal test. Pushing makes the Test with Advantage
    and adds 1d10 Warp Charge (and, on a Fumble, triggers Perils immediately)."""
    rng = rng or _default_rng
    test = resolve_test(profile, test_target, rng, advantage=1 if pushed else 0)
    push_roll = roll("1d10", rng).total if pushed else 0
    gained = warp_charge_gain(test, warp_rating, willpower_bonus, pushed=pushed, push_roll=push_roll)
    new_charge = current_charge + gained
    thr = threshold if threshold is not None else willpower_bonus
    rating_after = max(1, warp_rating - willpower_bonus) if test.critical else warp_rating
    return ManifestResult(
        test=test, power=power, warp_rating=rating_after, charge_gained=gained,
        warp_charge=new_charge, threshold=thr, over_threshold=new_charge > thr,
        pushed=pushed, immediate_perils=pushed and test.fumble,
    )


@dataclass(frozen=True, slots=True)
class TableOutcome:
    """A row drawn from a d100 consequence table (Perils of the Warp / Psychic Phenomena)."""

    roll: int        # the raw d100 face
    bonus: int       # +10 per Warp Charge over threshold (Perils) / shed (Phenomena)
    total: int       # roll + bonus — the value actually looked up
    name: str        # the effect's name, e.g. "Backlash"
    effect: str      # the German effect text the DM narrates
    corruption: int  # Corruption gained (Perils table; 0 for Phenomena)
    table: str       # "perils" | "phenomena"


def _lookup_band(table: list[dict], total: int) -> dict | None:
    """Find the row whose ``[min, max]`` band contains ``total``. The top row may omit ``max``
    (the open-ended ``171+`` band). Values below the lowest band clamp to the first row."""
    if not table:
        return None
    for row in table:
        hi = row.get("max")
        if total >= row["min"] and (hi is None or total <= hi):
            return row
    return table[0] if total < table[0]["min"] else table[-1]


def resolve_perils(
    profile: SystemProfile, *, over_by: int = 0, rng: random.Random | None = None
) -> TableOutcome:
    """Roll on the Perils of the Warp table (IM p.164): d100 + 10 per Warp Charge over the
    Threshold, then look up the effect + Corruption. Triggered when a psyker fails to contain
    Warp Charge above their Threshold (or Pushes into a Fumble)."""
    rng = rng or _default_rng
    face = rng.randint(1, 100)
    bonus = 10 * max(0, over_by)
    total = face + bonus
    row = _lookup_band(profile.perils_table(), total) or {}
    return TableOutcome(
        roll=face, bonus=bonus, total=total, name=row.get("name", "Unbekannt"),
        effect=row.get("effect", ""), corruption=int(row.get("corruption", 0)), table="perils",
    )


def resolve_phenomena(
    profile: SystemProfile, *, shed: int = 0, rng: random.Random | None = None, _depth: int = 0
) -> TableOutcome:
    """Roll on the Psychic Phenomena table (IM p.164): d100 + 10 per Warp Charge shed (Purgation).
    A result of 101–125 ("Psychic Breakthrough") rolls again; 126+ escalates to Perils of the
    Warp. The recursion is bounded so a misconfigured table can't loop forever."""
    rng = rng or _default_rng
    face = rng.randint(1, 100)
    bonus = 10 * max(0, shed)
    total = face + bonus
    row = _lookup_band(profile.phenomena_table(), total) or {}
    redirect = row.get("redirect")
    if redirect == "perils" and _depth < 4:
        return resolve_perils(profile, over_by=0, rng=rng)
    if redirect == "phenomena" and _depth < 4:
        return resolve_phenomena(profile, shed=0, rng=rng, _depth=_depth + 1)
    return TableOutcome(
        roll=face, bonus=bonus, total=total, name=row.get("name", "Unbekannt"),
        effect=row.get("effect", ""), corruption=int(row.get("corruption", 0)), table="phenomena",
    )


def describe_manifest_de(result: ManifestResult, *, character: str | None = None) -> str:
    """German one-line Manifest summary fed back so the DM narrates the power's effect:
    "🌀 Mortn manifestiert Smite (Ziel 35): 23 — Erfolg, 1 EG · Warp 2/4." Marks Push, Critical,
    Fumble and a Threshold breach so the model knows what just happened in the Warp."""
    who = character or "Psioniker"
    t = result.test
    head = f"🌀 {who} manifestiert {result.power} (Ziel {t.target}): {_face_str(t.roll)}"
    if t.critical:
        verdict = f"kritischer Erfolg, {t.degrees} EG"
    elif t.fumble:
        verdict = f"Patzer, {abs(t.degrees)} EG Fehlschlag"
    elif t.success:
        verdict = f"Erfolg, {t.degrees} EG"
    else:
        verdict = f"Fehlschlag, {abs(t.degrees)} EG"
    if result.pushed:
        verdict += ", gepusht"
    warp = f"Warp {result.warp_charge}/{result.threshold}"
    tail = ""
    if result.immediate_perils:
        tail = " — Push-Patzer: Perils of the Warp sofort!"
    elif result.over_threshold:
        tail = " — über der Schwelle: Perils-Probe am Zugende!"
    return f"{head} — {verdict} · {warp}{tail}."


def describe_perils_de(outcome: TableOutcome, *, character: str | None = None) -> str:
    """German one-line consequence summary for a Perils/Phenomena roll, fed back to the DM."""
    who = character or "Der Psioniker"
    label = "Perils of the Warp" if outcome.table == "perils" else "Psychische Phänomene"
    corr = f" · +{outcome.corruption} Verderbnis" if outcome.corruption else ""
    name = f" {outcome.name}" if outcome.name else ""
    return f"🜏 {label} für {who} ({_face_str(min(outcome.roll, 100))}+{outcome.bonus}={outcome.total}):{name}{corr}."
