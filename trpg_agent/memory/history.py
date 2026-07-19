"""Per-turn conversation autosave (D41 — crash recovery).

``DMBrain``'s history lives in memory; a crash loses the evening's conversational thread. World
state already persists separately (ADR 015: ``state.json``, atomic, saved per change). This is the
**third** session artifact: ``data/sessions/<channel_id>/history.jsonl`` — append-only, one JSON
line per completed DM turn (``{ts, user_msg, answer, redo}`` + optional replay fields, ADR 046)
plus typed journal events (``{"kind": …}``). It is code-owned like ``state.json``;
``characters.json`` stays the read-only sheet, so the ADR 015 split is not blurred. Restored on
``!join`` (only into an empty history), rotated on ``!leave``.

Pure file helpers (no Discord/asyncio) so they're unit-testable and run off the event loop via
``asyncio.to_thread`` in the cog.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def append_event(path: Path, record: dict) -> None:
    """Append one journal record as a JSON line (creates the parent dir). Append-only — no atomic
    rename needed; a torn final line is tolerated on load. Besides the per-turn records this also
    carries typed events (``{"kind": "session", …}``, ADR 046; ``{"kind": "scene", …}``, ADR 053)
    — :func:`load_recent` skips those (no ``user_msg``/``answer``), so old and new consumers
    coexist on one file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_turn(
    path: Path, *, ts: str, user_msg: str, answer: str, redo: bool = False,
    extra: dict | None = None,
) -> None:
    """Append one completed turn as a JSON line. ``redo=True`` marks a re-run so the
    loader replaces the prior turn instead of stacking (mirrors :meth:`DMBrain.redo`).
    ``extra`` carries the optional replay fields (raw LLM text, parsed markers, router
    verdict, … — ADR 046); the four core keys always win over a colliding extra."""
    record = dict(extra or {})
    record.update({"ts": ts, "user_msg": user_msg, "answer": answer, "redo": redo})
    append_event(path, record)


def load_recent(path: Path, max_turns: int) -> list[tuple[str, str]]:
    """Read the saved turns and return the last ``max_turns`` as ``(user_msg, answer)`` pairs.

    A ``redo`` record **replaces** the previous turn (so a restored session doesn't resurrect a
    superseded answer — same semantics as :meth:`DMBrain.redo`). Corrupt lines are skipped with a
    warning (an append-only file can have a torn tail). A missing file → ``[]``."""
    path = Path(path)
    if not path.is_file():
        return []
    turns: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            log.warning("history autosave: skipping a corrupt line in %s", path)
            continue
        user_msg, answer = rec.get("user_msg"), rec.get("answer")
        if user_msg is None or answer is None:
            continue
        if rec.get("redo") and turns:
            turns[-1] = (user_msg, answer)  # the redo replaced the prior turn
        else:
            turns.append((user_msg, answer))
    return turns[-max_turns:] if max_turns and max_turns > 0 else turns


def rotate(path: Path, *, stamp: str, debug: bool = False) -> Path | None:
    """Rename the history file to ``history.<stamp>.jsonl`` (keep it, don't delete) on ``!leave``,
    so the next session starts fresh while the record survives. No-op (``None``) if the file
    doesn't exist; otherwise returns the rotated path. ``debug=True`` (a debug-campaign run,
    ADR 055) names the archive ``history.<stamp>.debug.jsonl`` instead — the marker that keeps
    debug archives distinguishable from real session records forever after, in a session
    directory both kinds share."""
    path = Path(path)
    if not path.is_file():
        return None
    suffix = ".debug.jsonl" if debug else ".jsonl"
    target = path.with_name(f"history.{stamp}{suffix}")
    path.replace(target)
    return target
