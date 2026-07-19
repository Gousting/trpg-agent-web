"""Chekhov list — unresolved threads + callbacks (ADR 050).

Human GMs remember loose ends and play them back later ("the coin from session one? *That*
coin."). This module is that notebook: at wrap-up the ADR-044 extractor call additionally
proposes unresolved details of the session (and ids of existing threads the session resolved);
**code owns the list** — cap, dedupe, status transitions, eviction — following the
narrative-layer precedent of the recap and ADR 044/049: the *text* is LLM prose, but no hard
decision is ever derived from it and every list mutation happens here, deterministically.

Pure functions + dataclasses (no Discord, no LLM, no ``SessionRuntime``) — testable like
``rules/combat.py``. Persistence is ``data/sessions/<id>/chekhov.json`` beside the other
session files, written atomically like ``state.json``. The trigger seam (the ``!wrap``
extraction) and the prompt injection live in :mod:`dmbot.runtime`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Cap on OPEN threads (ADR 050): on overflow the oldest open thread with the lowest weight
# present is evicted — old weight-1 details nobody called back are the cheapest to lose.
MAX_OPEN = 20

# Resolved threads are display/history only — keep the newest few, the file stays small.
MAX_RESOLVED_KEPT = 20

# Per wrap-up extraction: at most this many NEW threads are accepted (session granularity —
# a human GM jots down a handful of loose ends per evening, not a page).
MAX_NEW_PER_EXTRACTION = 5

# One-sentence details — hard-truncated like the ADR-044 gists so the prompt block stays bounded.
DETAIL_MAX_CHARS = 200

# How many open threads ride in the DM prompt (ADR 050 #6): an offer, not a mandate.
TOP_K = 3

# Word-overlap threshold for the dedupe (normalised Jaccard) — deliberately blunt: a new
# thread that mostly re-words an existing one is the extractor re-noticing, not news.
_SIMILARITY_THRESHOLD = 0.6


@dataclass
class ChekhovThread:
    """One unresolved detail of a session (ADR 050) — narrative-layer prose like
    :class:`~dmbot.memory.state.NpcMemory`: the *detail* is LLM-extracted (or hand-seeded via
    ``!faden neu``), code stores/caps/serialises it, and no hard field is ever derived from it."""

    id: str                    # short sequential token ("t1", "t2", …) — what the commands take
    detail: str                # one German sentence
    origin_scene: str = ""     # scene id where the detail surfaced
    created_session: str = ""  # ISO date of the wrap-up that recorded it
    status: str = "open"       # "open" | "resolved"
    weight: int = 1            # 1–3 (3 = the strongest callback material)

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "detail": self.detail}
        if self.origin_scene:
            d["origin_scene"] = self.origin_scene
        if self.created_session:
            d["created_session"] = self.created_session
        if self.status != "open":
            d["status"] = self.status
        if self.weight != 1:
            d["weight"] = self.weight
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ChekhovThread":
        try:
            weight = min(3, max(1, int(d.get("weight", 1))))
        except (TypeError, ValueError):
            weight = 1
        status = str(d.get("status", "open") or "open")
        return cls(
            id=str(d.get("id", "") or ""),
            detail=str(d.get("detail", "") or ""),
            origin_scene=str(d.get("origin_scene", "") or ""),
            created_session=str(d.get("created_session", "") or ""),
            status=status if status in ("open", "resolved") else "open",
            weight=weight,
        )


@dataclass
class ChekhovList:
    """The per-channel thread list — insertion order IS age (ids are sequential, so older
    threads sort first without a timestamp)."""

    threads: list[ChekhovThread] = field(default_factory=list)

    # -- persistence (atomic, like WorldState.save) -------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "ChekhovList":
        """Load the list from ``path``; a missing or unreadable file is an empty list (the
        Chekhov list must never block a session)."""
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("chekhov: could not read %s — starting empty", path)
            return cls()
        raw = data.get("threads", []) if isinstance(data, dict) else []
        threads = [ChekhovThread.from_dict(t) for t in raw if isinstance(t, dict)]
        return cls(threads=[t for t in threads if t.id and t.detail])

    def save(self, path: Path) -> None:
        """Write atomically (temp + ``os.replace``), exactly like ``state.json`` — a crash
        mid-write must not corrupt the notebook."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"threads": [t.to_dict() for t in self.threads]}, ensure_ascii=False, indent=2
        )
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)  # atomic on Windows + POSIX
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- queries -------------------------------------------------------------------------------

    def open_threads(self) -> list[ChekhovThread]:
        return [t for t in self.threads if t.status == "open"]

    def resolved_threads(self) -> list[ChekhovThread]:
        return [t for t in self.threads if t.status == "resolved"]

    def find(self, thread_id: str) -> ChekhovThread | None:
        key = (thread_id or "").strip().lower()
        return next((t for t in self.threads if t.id.lower() == key), None)

    def top_open(self, k: int = TOP_K) -> list[ChekhovThread]:
        """The callback offer for the prompt: highest weight first, then **older first** —
        old threads are the best callbacks („die Münze aus Session 1")."""
        order = {id(t): i for i, t in enumerate(self.threads)}  # list order = age
        return sorted(self.open_threads(), key=lambda t: (-t.weight, order[id(t)]))[: max(0, k)]

    # -- mutations (code-owned) ------------------------------------------------------------------

    def next_id(self) -> str:
        highest = 0
        for t in self.threads:
            m = re.fullmatch(r"t(\d+)", t.id)
            if m:
                highest = max(highest, int(m.group(1)))
        return f"t{highest + 1}"

    def add_thread(
        self, detail: str, *, weight: int = 1, origin_scene: str = "", created_session: str = ""
    ) -> ChekhovThread | None:
        """Add one thread — deduped against ALL existing threads (open **and** resolved, so a
        resolved coin doesn't come back), detail truncated, weight clamped, the open cap
        enforced by evicting the oldest lowest-weight open thread. Returns the new thread, or
        ``None`` when the detail was empty or a near-duplicate."""
        detail = _truncate_detail(detail)
        if not detail:
            return None
        for existing in self.threads:
            if is_similar(detail, existing.detail):
                log.info("chekhov: '%s' ähnelt [%s] — übersprungen", detail, existing.id)
                return None
        thread = ChekhovThread(
            id=self.next_id(),
            detail=detail,
            origin_scene=origin_scene,
            created_session=created_session,
            weight=min(3, max(1, weight)),
        )
        self.threads.append(thread)
        self._evict_over_cap()
        self._trim_resolved()
        return thread

    def resolve(self, thread_id: str) -> ChekhovThread | None:
        """Flip an open thread to resolved (recognised, not forced — ADR 050 #5). Unknown id or
        already resolved → ``None``."""
        thread = self.find(thread_id)
        if thread is None or thread.status != "open":
            return None
        thread.status = "resolved"
        self._trim_resolved()
        return thread

    def remove(self, thread_id: str) -> ChekhovThread | None:
        thread = self.find(thread_id)
        if thread is not None:
            self.threads.remove(thread)
        return thread

    def _evict_over_cap(self) -> None:
        while len(self.open_threads()) > MAX_OPEN:
            open_threads = self.open_threads()
            lightest = min(t.weight for t in open_threads)
            victim = next(t for t in open_threads if t.weight == lightest)  # list order = oldest
            self.threads.remove(victim)
            log.info("chekhov: Cap erreicht — ältester Faden mit Gewicht %d fliegt: [%s] %s",
                     victim.weight, victim.id, victim.detail)

    def _trim_resolved(self) -> None:
        resolved = self.resolved_threads()
        for victim in resolved[: max(0, len(resolved) - MAX_RESOLVED_KEPT)]:
            self.threads.remove(victim)


# -- dedupe -----------------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\wäöüß ]", " ", (text or "").casefold()).split())


def is_similar(a: str, b: str) -> bool:
    """Blunt near-duplicate check (ADR 050 #5): normalised substring containment OR word-set
    Jaccard overlap ≥ threshold. Good enough — a false negative costs one redundant thread the
    cap prunes eventually; a false positive drops a re-worded re-notice."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    wa, wb = set(na.split()), set(nb.split())
    union = wa | wb
    return bool(union) and len(wa & wb) / len(union) >= _SIMILARITY_THRESHOLD


def _truncate_detail(detail: str) -> str:
    detail = " ".join((detail or "").split())
    if len(detail) <= DETAIL_MAX_CHARS:
        return detail
    return detail[: DETAIL_MAX_CHARS - 1].rstrip() + "…"


# -- extraction (schema fragment + input section + apply) --------------------------------------

# Merged into the ADR-044 EXTRACT_SCHEMA at wrap-up (ADR 050 #3) — `new`/`resolved` may be
# empty, but the section itself is required so the model always answers the question.
CHEKHOV_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "new": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "detail": {"type": "string"},
                    "weight": {"type": "integer"},
                },
                "required": ["detail"],
            },
        },
        "resolved": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["new", "resolved"],
}


def build_chekhov_section(
    open_threads: list[ChekhovThread], earlier_turns: list[dict[str, str]]
) -> str:
    """The wrap-up extractor's extra input (ADR 050 #4): the current open threads (with ids,
    for resolution detection and against re-recording) and the session history *before* the
    scene window — clearly labelled as threads-only context (the NPC-memory part of the call
    stays bound to the scene transcript above)."""
    lines = ["", "Bisherige lose Fäden (offen):"]
    if open_threads:
        for t in open_threads:
            scene = f" (Szene: {t.origin_scene})" if t.origin_scene else ""
            lines.append(f"- [{t.id}] {t.detail}{scene}")
    else:
        lines.append("- (keine)")
    earlier = [
        f"{'Spielleitung' if msg.get('role') == 'assistant' else 'Spieler'}: {content}"
        for msg in earlier_turns
        if (content := (msg.get("content") or "").strip())
    ]
    if earlier:
        lines.append("")
        lines.append(
            "Früherer Verlauf dieser Sitzung (bereits fürs NSC-Gedächtnis ausgewertet — "
            "hier NUR nach losen Fäden und deren Auflösung durchsuchen):"
        )
        lines.extend(earlier)
    return "\n".join(lines)


def apply_chekhov(
    clist: ChekhovList,
    payload: object,
    *,
    origin_scene: str = "",
    created_session: str = "",
) -> tuple[int, int]:
    """Apply the extractor's ``chekhov`` section — all hard effects here, in code (ADR 050 #5):
    resolutions are flipped first (an unknown id is dropped + logged), then up to
    :data:`MAX_NEW_PER_EXTRACTION` new threads are added through the deduping, capped
    :meth:`ChekhovList.add_thread`. Defensive end to end: a malformed payload is a no-op.
    Returns ``(new_count, resolved_count)``."""
    if not isinstance(payload, dict):
        if payload is not None:
            log.info("chekhov: unbrauchbare Extraktions-Sektion (%r) — übersprungen", type(payload))
        return (0, 0)
    resolved_count = 0
    for thread_id in payload.get("resolved", []) or []:
        if not isinstance(thread_id, str):
            continue
        if clist.resolve(thread_id) is not None:
            resolved_count += 1
            log.info("chekhov: Faden [%s] als aufgelöst markiert", thread_id.strip().lower())
        else:
            log.info("chekhov: unbekannte/erledigte Faden-ID %r — übersprungen", thread_id)
    new_count = 0
    raw_new = payload.get("new", []) or []
    if isinstance(raw_new, list):
        for item in raw_new[:MAX_NEW_PER_EXTRACTION]:
            if not isinstance(item, dict):
                continue
            try:
                weight = int(item.get("weight", 1))
            except (TypeError, ValueError):
                weight = 1
            thread = clist.add_thread(
                str(item.get("detail", "") or ""),
                weight=weight,
                origin_scene=origin_scene,
                created_session=created_session,
            )
            if thread is not None:
                new_count += 1
                log.info("chekhov: neuer Faden [%s] (Gewicht %d): %s",
                         thread.id, thread.weight, thread.detail)
    return (new_count, resolved_count)


# -- prompt injection ---------------------------------------------------------------------------


def chekhov_block_de(threads: list[ChekhovThread]) -> str:
    """The compact callback offer for the DM prompt (ADR 050 #6) — pass ``top_open()`` in.
    Nothing to offer → ''."""
    if not threads:
        return ""
    lines = [
        "## Lose Fäden (unaufgelöste Details früherer Sitzungen — greife einen auf, "
        "wenn er sich natürlich anbietet; nicht erzwingen, nicht alle auf einmal)"
    ]
    for t in threads:
        tag = "(wichtig) " if t.weight >= 3 else ""
        lines.append(f"- {tag}{t.detail}")
    return "\n".join(lines)
