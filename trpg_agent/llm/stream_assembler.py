"""Streaming sentence-assembler + the shared answer-finalisation seam (ADR 017).

Pure and ``DMBrain``-state-free, extracted from ``orchestrator.py`` (ADR 034, E4).
:func:`finalize_answer` is the single post-processing path shared by the batch turn
(``DMBrain._generate`` / ``_chat_once``) and the streaming :meth:`StreamAssembler.finish`, so the
stored history is identical for the same raw text (the parity guarantee). :class:`StreamAssembler`
turns raw LLM deltas into speakable, already-sanitised sentences with the ADR-017 hold-back rules.
``DMBrain`` re-imports both from ``orchestrator`` (back-compat shim).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .sanitize import (
    _cut_at_labels,
    _sanitize,
    _sanitize_leading,
    _strip_leading_label,
    _trim_to_last_sentence,
)
from ..rules.marker import (
    ClockTickRequest,
    ErledigtRequest,
    ManifestRequest,
    SceneRequest,
    TestRequest,
    ZeitRequest,
    clean_narration,
    extract_all,
)
from ..rules.profile import SystemProfile
from ..tts.textsplit import split_completed

log = logging.getLogger(__name__)


def finalize_answer_markers(
    raw: str, labels: list[str], profile: SystemProfile | None
) -> tuple[str, dict[str, list]]:
    """The full non-streaming post-processing of a raw LLM answer → (clean spoken answer,
    ``{kind: parsed requests}`` keyed by the marker registry, ADR 051). The single source of
    truth shared by the batch path (:meth:`DMBrain._generate`) and the streaming assembler's
    :meth:`StreamAssembler.finish` — so the two can never drift and the stored history is
    identical for the same raw text (the parity guarantee, ADR 017)."""
    answer = _sanitize(_cut_at_labels(raw, labels)) or _sanitize(raw)
    answer = _strip_leading_label(answer, labels)  # kill a leaked leading "Name:"/"DM:" label
    answer, markers = extract_all(answer, profile)  # strip every <<…>> marker in registry order
    return _trim_to_last_sentence(answer), markers


def finalize_answer(
    raw: str, labels: list[str], profile: SystemProfile | None
) -> tuple[
    str, list[TestRequest], list[ManifestRequest], list[SceneRequest],
    list[ErledigtRequest], list[ClockTickRequest], list[ZeitRequest],
]:
    """Tuple view of :func:`finalize_answer_markers` — the historical public shape (answer,
    tests, manifests, scenes, erledigt, uhr, zeit), kept for existing callers and tests
    (ADR 051). The tuple order is the registry order."""
    answer, markers = finalize_answer_markers(raw, labels, profile)
    return (answer, *markers.values())


# --- Streaming assembler (ADR 017) --------------------------------------------------------------

# Hold the first chunk until it's a full sentence or this many chars, so a leading meta-preamble /
# role label is strippable before anything is spoken.
_FIRST_CHUNK_MIN_CHARS = 80


def _open_marker_index(text: str) -> int | None:
    """Index of an *unclosed* ``<<`` (a ``<<TEST …>>`` marker still arriving), or None — so the
    streaming view can withhold the dangling marker fragment and never speak a partial ``<<``."""
    i = text.rfind("<<")
    if i == -1 or ">>" in text[i:]:
        return None
    return i


@dataclass
class StreamResult:
    """Outcome of :meth:`StreamAssembler.finish`: sentences not yet spoken, the canonical stored
    answer (history parity), and the marker requests parsed from the whole turn, keyed by the
    registry ``kind`` (ADR 051). Per-kind attribute access (``result.tests``, ``result.zeit`` …)
    is kept as a dynamic view into ``markers`` for existing callers and tests."""

    remaining: list[str]
    answer: str
    markers: dict[str, list]

    def __getattr__(self, name: str) -> list:
        # Back-compat accessors (ADR 051): result.<kind> reads markers[<kind>]. Only reached
        # when normal attribute lookup fails, so the real fields stay untouched.
        if name == "markers":  # not set yet (mid-unpickle) — must not recurse into itself
            raise AttributeError(name)
        try:
            return self.markers[name]
        except KeyError:
            raise AttributeError(name) from None


class StreamAssembler:
    """Turns a stream of raw LLM deltas into speakable sentences while preserving the *exact*
    non-streaming sanitisation (ADR 017). Pure + unit-testable against a list of fake deltas.

    Hold-back rules:
    - the **first** sentence is withheld until it's complete (one sentence or ``_FIRST_CHUNK_MIN_CHARS``
      chars) so a leading meta-preamble / role label is strippable before anything is spoken;
    - the **latest** completed sentence is always held back (emit N only when N+1 exists) so the
      trailing strips (parenthetical, 'Was tut ihr?', mid-word cut) apply before it's spoken;
    - text is withheld from any unmatched ``<<`` — a ``<<TEST …>>`` marker may span deltas;
    - a mid-text speaker label (``_cut_at_labels``) sets :attr:`stopped` so the caller aborts the
      HTTP stream and only the pre-label narration is kept.

    :meth:`finish` recomputes the answer with :func:`finalize_answer` on the accumulated raw, so
    history parity holds by construction; ``remaining`` is whatever of that answer wasn't spoken.
    """

    def __init__(self, labels: list[str], profile: SystemProfile | None) -> None:
        self._labels = labels
        self._profile = profile
        self._raw = ""
        self._released = False
        self._emitted: list[str] = []
        self.stopped = False

    @property
    def raw(self) -> str:
        return self._raw

    def feed(self, delta: str) -> list[str]:
        """Accumulate ``delta`` and return any newly-speakable sentences (already sanitised)."""
        if self.stopped:
            return []
        self._raw += delta
        cut = _cut_at_labels(self._raw, self._labels)
        if len(cut) < len(self._raw.strip()):
            self.stopped = True  # a speaker label appeared mid-text → caller aborts the stream
        body = self._body(cut)
        if body is None:
            return []
        sentences, _tail = split_completed(body)
        emittable = sentences[:-1] if sentences else []  # hold back the latest completed sentence
        new = emittable[len(self._emitted):]
        self._emitted.extend(new)
        return new

    def _body(self, cut: str) -> str | None:
        """The leading-sanitised, marker-stripped speakable view of the cut buffer, or None while
        the first chunk is still being held back."""
        text = cut.replace("*", "")
        if "<<" not in text:
            # Fast path: no marker can be present, so the three regex extractors would each only run
            # their trailing _clean over the whole buffer (O(n) × 3 per delta → O(n²) across a turn).
            # Apply that same tidy once instead — byte-identical output, the marker scans skipped.
            text = clean_narration(text)
        else:
            idx = _open_marker_index(text)
            if idx is not None:
                text = text[:idx]  # withhold from an unmatched "<<" (a marker may span deltas)
            # Strip every complete <<…>> marker (never spoken) in registry order (ADR 051);
            # the parsed requests are recomputed canonically in finish(), so discard them here.
            text, _ = extract_all(text, self._profile)
        text = _sanitize_leading(text)
        text = _strip_leading_label(text, self._labels)
        if not self._released:
            sentences, _tail = split_completed(text)
            if not sentences and len(text) < _FIRST_CHUNK_MIN_CHARS:
                return None
            self._released = True
        return text

    def finish(self) -> StreamResult:
        """Stream ended (or aborted): compute the canonical answer + marker requests and return
        whatever of it hasn't been spoken yet (the held-back tail / final sentence)."""
        answer, markers = finalize_answer_markers(self._raw, self._labels, self._profile)
        sentences, tail = split_completed(answer)
        all_sentences = [s for s in (*sentences, tail) if s]
        if all_sentences[: len(self._emitted)] == self._emitted:
            remaining = all_sentences[len(self._emitted):]
        else:
            # The canonical answer diverged from what we already spoke — only the rare mid-text
            # self-correction frame ("Hier ist die korrekte Antwort: …") or a stop-label edit can
            # do this. Speak the canonical remainder so the real answer is still heard; history
            # already stores the canonical text, so parity is intact.
            log.warning(
                "streaming: spoken text diverged from the finalized answer (self-correction / "
                "stop-label) — speaking the canonical remainder; %d sentences already spoken",
                len(self._emitted),
            )
            remaining = all_sentences
        return StreamResult(remaining=remaining, answer=answer, markers=markers)
