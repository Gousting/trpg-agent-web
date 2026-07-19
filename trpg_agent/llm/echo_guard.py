"""Echo / self-repetition guards (D43 / ADR 018, W4).

Pure predicates (``re`` + ``difflib`` only), extracted from ``orchestrator.py`` (ADR 034) so the
echo/repeat tuning is a small focused file. :func:`is_echo` catches the model parroting a player
line; :func:`is_self_repetition` catches it re-narrating its own previous answer; the
``_*_NUDGE`` / ``_ROLL_DIRECTIVE`` strings are appended to the retry / results-only prompts by
``DMBrain``.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


# --- Echo guard (D43 / ADR 018) ------------------------------------------------------------------

# Appended to the retry prompt when the model parroted a player line instead of narrating.
_ECHO_NUDGE = (
    "Antworte als Spielleitung: Beschreibe, was daraufhin in der Szene geschieht, "
    "und wiederhole nicht die Worte der Spielenden."
)

# Appended when the model re-narrated its own previous answer (W4: players asked "warum hat er
# das zweimal gesagt?" — seen live as a near-verbatim scene re-description on a direct question).
_REPEAT_NUDGE = (
    "Beantworte die konkrete Frage der Spielenden direkt und knapp; "
    "wiederhole nicht deine letzte Beschreibung."
)

# Explicit directive on a results-only (post-roll) turn — without it the model sees a bare
# "[Würfel] …" line and tends to predict the *next player line* instead of narrating (seen live
# 2026-06-12: three identical echo turns poisoned the history).
_ROLL_DIRECTIVE = (
    "Beschreibe als Spielleitung kurz die Folgen dieses Würfelergebnisses in der Szene."
)


def _normalize_echo(text: str) -> str:
    """Case/punctuation-insensitive view for the echo comparison."""
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def is_echo(answer: str, user_msg: str) -> bool:
    """True when ``answer`` merely parrots a line of this turn's ``user_msg`` — the model
    predicted the next table line ("Pr0degie: Ich greife den Kultisten an.") instead of narrating.
    Once such an echo lands in history it self-reinforces, so the caller retries/suppresses.
    Compares each user line (with and without its ``Name:``/``[Würfel] …:`` lead) normalized;
    an echo is an exact match, the answer being a fragment of the line, or the line covering
    ≥90% of the answer. Lines under 10 normalized chars never count (too short to call)."""
    norm_answer = _normalize_echo(answer)
    if not norm_answer:
        return False
    for line in user_msg.splitlines():
        body = line.split(":", 1)[1] if ":" in line else line
        for candidate in (line, body):
            norm_line = _normalize_echo(candidate)
            if len(norm_line) < 10:
                continue
            if (
                norm_answer == norm_line
                or norm_answer in norm_line
                or (norm_line in norm_answer and len(norm_line) >= 0.9 * len(norm_answer))
            ):
                return True
    return False


def is_self_repetition(answer: str, previous_answer: str) -> bool:
    """True when ``answer`` re-narrates the DM's **own previous answer** nearly verbatim — the W4
    failure (live 2026-06-12: asked "Warum sind wir hier?", the model re-told the prior scene
    description with only pronoun swaps). Fuzzy, not substring: pronoun/conjugation edits survive
    a SequenceMatcher ratio. Short answers are exempt — "Du triffst." may legitimately recur."""
    norm_new = _normalize_echo(answer)
    norm_prev = _normalize_echo(previous_answer)
    if len(norm_new) < 60 or len(norm_prev) < 60:
        return False
    return SequenceMatcher(None, norm_new, norm_prev).ratio() >= 0.75
