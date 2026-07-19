"""Deterministic "is this opening weak?" check for the ``!intro`` retry (ADR 041 follow-up).

The 12B model's opening monologue is high-variance: usually it weaves in place, mission and every
player figure, but sometimes it comes out short/generic or skips a character entirely. The director
brief + a pinned temperature (ADR 041 + addendum) reduce this but can't guarantee it, so the batch
``!intro`` path regenerates **once** when this returns ``True``. Pure + side-effect-free so the
retry policy is unit-testable; the cog/orchestrator owns the actual one-shot retry.
"""

from __future__ import annotations

import re

# A real multi-paragraph opening (it runs on the larger DM_INTRO_NUM_PREDICT budget) is far longer
# than this; the observed failure is a generic one-or-two-sentence turn, which falls well under it.
_MIN_INTRO_CHARS = 280

# Appended to the director instruction on the single retry — a firmer nudge toward the properties
# the first attempt missed (length + every figure), without changing the persona.
INTRO_RETRY_NUDGE = (
    "[Regie] Der erste Anlauf war zu knapp oder hat eine Figur ausgelassen. Schreibe den "
    "Eröffnungs-Monolog jetzt ausführlich (mehrere Absätze) und beziehe JEDE genannte Figur "
    "namentlich mit einem eigenen Moment ein. Kein Meta, keine Aufzählung."
)


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else ""


def is_weak_intro(text: str, roster_names: list[str]) -> bool:
    """True when the opening should be regenerated: too short (generic filler) **or** a roster
    figure is never named. ``roster_names`` are the full character names; a figure counts as present
    when its first name appears as a whole word (the DM may address "Fridolin", not the surname).
    An empty roster reduces this to the length check."""
    stripped = (text or "").strip()
    if len(stripped) < _MIN_INTRO_CHARS:
        return True
    low = stripped.casefold()
    for name in roster_names:
        first = _first_name(name)
        if not first:
            continue
        # Tolerate the German genitive/possessive 's' ("Seskins Hand", "Jürgens Blick") so a good
        # opening that only names a figure in that form isn't judged weak and regenerated for nothing.
        if not re.search(rf"\b{re.escape(first.casefold())}s?\b", low):
            return True
    return False
