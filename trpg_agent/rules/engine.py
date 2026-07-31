"""Generic dice + resolution engine (ADR 005) — the deterministic heart of the project.

Golden rule #2: dice (RNG) **and** their resolution (success, degrees, crit, damage) are
computed here, never by the LLM. The engine is system-agnostic: it takes a numeric target
and a :class:`~trpg_agent.rules.profile.SystemProfile` and applies the profile's resolution kind.
The current legacy profile set still includes Imperium-style helpers; other
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


# NOTE (2026-07-31 doc/dead-code audit): everything below this point in the original file was
# unused Warhammer-40k "Imperium" Psyker/Warp/Perils machinery (with German-language narration
# strings) reused from a different Discord-bot project. Nothing in the live COC web game or the
# test suite called any of it (`describe_result_de`, `resolve_damage`/`DamageResult`/
# `describe_damage_de`, `ManifestResult`/`warp_charge_gain`/`resolve_manifest`, `TableOutcome`/
# `resolve_perils`/`resolve_phenomena`, `describe_manifest_de`/`describe_perils_de`, `_face_str`).
# Removed as dead code; the real COC 7e skill-check/combat/sanity/luck logic lives in
# `rules/coc.py` / `rules/combat.py` / `rules/sanity.py` / `rules/luck.py`.
