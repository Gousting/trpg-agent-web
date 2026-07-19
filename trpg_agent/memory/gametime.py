"""In-game time — pure helpers for the minutes-since-campaign-start counter (ADR 048).

The internal model is one int: ``WorldState.time_minutes``, minutes since day 1, 00:00.
Everything else here is derived rendering (day/clock/phase), duration parsing for the
``<<ZEIT +4h>>`` marker and the ``!zeit``/``!frist`` commands, and the coarse German
remaining-time phrases for deadlines. No state, no Discord, no LLM — unit-tested alone.

Deliberately decoupled from :mod:`dmbot.memory.state` (plain values in, strings out), so
``rules/marker.py`` can import the duration parser without a rules→memory→rules cycle.
"""

from __future__ import annotations

import re

DAY_MINUTES = 1440
# Fresh campaigns (and migrated legacy states, ADR 048 #2) start day 1, 08:00.
DEFAULT_START_MINUTES = 8 * 60
# Morning jump target for `!zeit tag` (and the reference "morning" of the phase table).
MORNING_MINUTES = 8 * 60
# Hard clamp for one <<ZEIT>> marker turn (ADR 048 #4): bigger jumps are command territory.
MAX_MARKER_ADVANCE_MINUTES = 12 * 60

# Tolerant duration grammar: optional +, int or decimal amount (comma or dot), German/short
# units. Matches "+30m", "4h", "2 Std", "90 min", "1,5h". Anchored — the whole payload must
# be a duration, so a garbled marker fails parse instead of half-matching.
_DURATION_RE = re.compile(
    r"^\+?\s*(\d+(?:[.,]\d+)?)\s*(m|min|minute|minuten|h|std|stunde|stunden)\.?$",
    re.IGNORECASE,
)
_HOUR_UNITS = ("h", "std", "stunde", "stunden")

# Day-phase boundaries (ADR 048 #3), by hour-of-day: [start, end) — Nacht wraps midnight.
_PHASES_DE = (("Morgen", 5, 11), ("Tag", 11, 17), ("Abend", 17, 22))


def parse_duration_de(text: str) -> int | None:
    """Parse a German duration ("+30m", "4h", "2 Std", "1,5h") to whole minutes.

    ``None`` when the text isn't a duration or rounds to zero — the caller treats that as
    an unparseable/rejected proposal (time never advances by nothing, ADR 048 #4)."""
    m = _DURATION_RE.match(text.strip())
    if m is None:
        return None
    amount = float(m.group(1).replace(",", "."))
    minutes = amount * 60 if m.group(2).lower() in _HOUR_UNITS else amount
    minutes = int(round(minutes))
    return minutes if minutes > 0 else None


def render_time_de(minutes: int) -> str:
    """'Tag 2, 14:30' — the canonical human-readable form (also mirrored into
    ``WorldState.time_ingame`` whenever code advances the counter)."""
    minutes = max(0, minutes)
    day, rest = divmod(minutes, DAY_MINUTES)
    return f"Tag {day + 1}, {rest // 60:02d}:{rest % 60:02d}"


def day_phase_de(minutes: int) -> str:
    """'Morgen' | 'Tag' | 'Abend' | 'Nacht' from the hour of day (boundaries: ADR 048 #3)."""
    hour = (max(0, minutes) % DAY_MINUTES) // 60
    for name, start, end in _PHASES_DE:
        if start <= hour < end:
            return name
    return "Nacht"


def render_time_phase_de(minutes: int) -> str:
    """'Tag 2, 14:30 (Tag)' — time + phase, the form the prompt and the panel show."""
    return f"{render_time_de(minutes)} ({day_phase_de(minutes)})"


def next_morning(minutes: int) -> int:
    """The counter value of the next 08:00 (`!zeit tag`): later today when it is still
    night-before-morning, otherwise tomorrow. Always strictly in the future."""
    minutes = max(0, minutes)
    day, rest = divmod(minutes, DAY_MINUTES)
    if rest < MORNING_MINUTES:
        return day * DAY_MINUTES + MORNING_MINUTES
    return (day + 1) * DAY_MINUTES + MORNING_MINUTES


def remaining_de(due_minutes: int, now_minutes: int) -> str:
    """Coarse German remaining time ('noch ~20 Min' / 'noch ~3 Std' / 'noch ~2 Tage'), or
    'ABGELAUFEN'. Coarse on purpose (ADR 048 #7): the DM should speak in fiction units."""
    diff = due_minutes - now_minutes
    if diff <= 0:
        return "ABGELAUFEN"
    if diff < 60:
        return f"noch ~{diff} Min"
    if diff < DAY_MINUTES:
        hours = max(1, round(diff / 60))
        return f"noch ~{hours} Std"
    days = max(1, round(diff / DAY_MINUTES))
    return f"noch ~{days} Tag" + ("" if days == 1 else "e")


def deadline_line_de(deadline_id: str, label: str, due_minutes: int, now_minutes: int) -> str:
    """'[zug] Der Zug nach Hive Sibellus — noch ~1 Tag' — the compact prompt/panel line.
    The id rides in brackets like clock/element ids (`!frist weg <id>` takes it)."""
    return f"[{deadline_id}] {label} — {remaining_de(due_minutes, now_minutes)}"


def deadline_note_de(label: str) -> str:
    """The one-shot ``[Regie]`` directive queued when a deadline expires (ADR 048 #8) —
    the same injection mechanism a full clock uses (ADR 047)."""
    return (
        f"Die Frist „{label}“ ist verstrichen — die angekündigte Konsequenz tritt JETZT ein. "
        "Spiele sie in deinem nächsten Beitrag als Ereignis in der Szene ein."
    )
