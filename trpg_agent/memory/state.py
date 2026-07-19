"""World state — the 'hard facts' half of memory (architecture §7, golden rule #3).

Per-channel mutable game state in ``data/sessions/<channel_id>/state.json``: the party's current
wounds / conditions / inventory, the NPCs in play, open quests, location, in-game time, and the
stored session recap. Advanced **deterministically by code** (e.g. HP after damage) — never
written from LLM free text. The one narrative field is ``recap``, produced by the wrap-up
summariser (LLM) but *stored* by code (memory split, golden rule #3).

Design (ADR 015 — "split"): the player **sheets** (characteristics, skills, ``max_wounds``,
aliases) stay in the read-only ``characters.json`` — the source transferred once from the sheets
(ADR 004). This ``state.json`` is the code-owned **mutable layer**, seeded once from that sheet on
the first join. Code only ever writes ``state.json``, so the sheet stays pristine and a session
resets by deleting ``state.json``.

Pure data + pure functions, unit-tested without Discord or the LLM. Combat *math* lives in
:mod:`dmbot.rules.engine`; this module only applies the resulting number and persists it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .gametime import (
    DEFAULT_START_MINUTES,
    day_phase_de,
    deadline_line_de,
    remaining_de,
    render_time_de,
    render_time_phase_de,
)

if TYPE_CHECKING:
    from ..rules.characters import CharacterStore

log = logging.getLogger(__name__)

# Condition set on a combatant whose wounds reach 0. German (play language); the caller can pass a
# system-specific word, but this is the sensible default for IM ("kampfunfähig" → out of the fight).
DOWNED_CONDITION = "kampfunfähig"

# NPC attitude scale (ADR 044) — the fixed, code-owned axis attitude drift moves along. Stored
# tokens are English (code); the prompt renders German labels. Order matters: index distance is
# what step_attitude clamps.
ATTITUDE_SCALE = ("hostile", "wary", "neutral", "friendly", "loyal")

# Hard cap on stored memories per NPC (ADR 044) — keeps state.json and the prompt block bounded.
NPC_MEMORY_CAP = 30

# Hard cap on stored agenda steps per NPC (ADR 049) — a timeline, so plain FIFO: the newest 10
# are the useful ones, older steps age out (no importance tiers like memories).
AGENDA_LOG_CAP = 10


@dataclass
class AgendaStep:
    """One offscreen move an agenda NPC made toward its goal (ADR 049) — narrative-layer prose
    like :class:`NpcMemory`: the *text* is LLM-extracted, code stores/caps/serialises it, and no
    hard field is ever derived from it (golden rule #3). ``ts_ingame`` is the rendered ADR-048
    clock at extraction time ("Tag 2, 14:30") — display data, never parsed back."""

    ts_ingame: str
    text: str

    def to_dict(self) -> dict:
        d: dict = {"text": self.text}
        if self.ts_ingame:
            d["ts_ingame"] = self.ts_ingame
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AgendaStep":
        return cls(
            ts_ingame=str(d.get("ts_ingame", "") or ""),
            text=str(d.get("text", "") or ""),
        )


@dataclass
class NpcMemory:
    """One thing an NPC remembers (ADR 044) — a narrative-layer entry, like the recap: the *text*
    is LLM-extracted, but code stores, caps and serialises it, and no hard field is ever derived
    from it (golden rule #3). ``believed`` covers player lies: the NPC stores what it *believes*;
    a revealed lie flips it to False (the NPC now knows it was lied to)."""

    about: list[str]        # ["party"] or ["pc:Kael"] (several possible)
    gist: str               # 1–3 German sentences
    quote: str = ""         # verbatim key quote — only for promises/lies/threats, else empty
    believed: bool = True   # False = the NPC knows by now this was a lie
    importance: int = 3     # 1–5
    source: str = "direct"  # "direct" | "gossip"
    scene: str = ""         # scene id of origin
    ts: str = ""            # ISO timestamp

    def to_dict(self) -> dict:
        d: dict = {"about": list(self.about), "gist": self.gist}
        if self.quote:
            d["quote"] = self.quote
        if not self.believed:
            d["believed"] = False
        if self.importance != 3:
            d["importance"] = self.importance
        if self.source != "direct":
            d["source"] = self.source
        if self.scene:
            d["scene"] = self.scene
        if self.ts:
            d["ts"] = self.ts
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NpcMemory":
        return cls(
            about=[str(a) for a in d.get("about", []) or []],
            gist=str(d.get("gist", "") or ""),
            quote=str(d.get("quote", "") or ""),
            believed=bool(d.get("believed", True)),
            importance=min(5, max(1, int(d.get("importance", 3) or 3))),
            source=str(d.get("source", "direct") or "direct"),
            scene=str(d.get("scene", "") or ""),
            ts=str(d.get("ts", "") or ""),
        )


@dataclass
class Combatant:
    """A character or NPC for state/damage purposes. Mutable — this is the code-owned layer.

    ``armour`` and ``toughness_bonus`` are the soak inputs the engine subtracts from damage. For a
    **player** they're derived from the sheet at damage time (TB = tens of Toughness), so a PC entry
    usually leaves ``toughness_bonus=0`` here; an **NPC** carries its own soak values directly.
    """

    name: str
    wounds: int
    max_wounds: int
    conditions: list[str] = field(default_factory=list)
    inventory: list[str] = field(default_factory=list)
    armour: int = 0
    toughness_bonus: int = 0
    is_npc: bool = False
    attitude: str = ""  # NPCs only (§7): "hostile" | "neutral" | … ; empty for PCs
    # Psyker resource (ADR 022): accumulated Warp Charge and the powers being Sustained. Mutable +
    # code-owned, like wounds — advanced by the Manifest/Purgation flow, never by LLM free text.
    warp_charge: int = 0
    sustained_powers: list[str] = field(default_factory=list)
    # NPC memory (ADR 044): gossip group + what this NPC remembers. ``faction`` is authored data
    # (npcs.json statblock / manual), never LLM output; ``memories`` is the capped narrative layer.
    faction: str = ""
    memories: list[NpcMemory] = field(default_factory=list)
    # NPC agenda (ADR 049): a non-empty ``goal`` marks this NPC as an agenda NPC — it pursues
    # the goal offscreen, one extracted step per scene change. ``goal`` is human/authored data
    # (``!agenda`` / npcs.json ``goal_de``), never LLM output; ``agenda_log`` is the capped
    # narrative timeline of its offscreen moves.
    goal: str = ""
    agenda_log: list[AgendaStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "wounds": self.wounds, "max_wounds": self.max_wounds}
        if self.conditions:
            d["conditions"] = list(self.conditions)
        if self.inventory:
            d["inventory"] = list(self.inventory)
        if self.armour:
            d["armour"] = self.armour
        if self.toughness_bonus:
            d["toughness_bonus"] = self.toughness_bonus
        if self.attitude:
            d["attitude"] = self.attitude
        if self.warp_charge:
            d["warp_charge"] = self.warp_charge
        if self.sustained_powers:
            d["sustained_powers"] = list(self.sustained_powers)
        if self.faction:
            d["faction"] = self.faction
        if self.memories:
            d["memories"] = [m.to_dict() for m in self.memories]
        if self.goal:
            d["goal"] = self.goal
        if self.agenda_log:
            d["agenda_log"] = [s.to_dict() for s in self.agenda_log]
        return d

    @classmethod
    def from_dict(cls, d: dict, *, is_npc: bool = False) -> "Combatant":
        max_w = int(d.get("max_wounds", d.get("wounds", 0)) or 0)
        return cls(
            name=d["name"],
            wounds=int(d.get("wounds", max_w) if d.get("wounds") is not None else max_w),
            max_wounds=max_w,
            conditions=list(d.get("conditions", []) or []),
            inventory=list(d.get("inventory", []) or []),
            armour=int(d.get("armour", 0) or 0),
            toughness_bonus=int(d.get("toughness_bonus", 0) or 0),
            is_npc=is_npc,
            attitude=str(d.get("attitude", "") or ""),
            warp_charge=int(d.get("warp_charge", 0) or 0),
            sustained_powers=list(d.get("sustained_powers", []) or []),
            faction=str(d.get("faction", "") or ""),
            memories=[NpcMemory.from_dict(m) for m in d.get("memories", []) or []],
            goal=str(d.get("goal", "") or ""),
            agenda_log=[AgendaStep.from_dict(s) for s in d.get("agenda_log", []) or []],
        )

    def add_memory(self, memory: NpcMemory) -> None:
        """Append a memory, pruning past :data:`NPC_MEMORY_CAP` (ADR 044): the lowest-importance
        entry goes first (tie: the oldest). ``believed: False`` entries and importance 5 are
        prune-protected — a known lie or a promise must not scroll out. If *everything* is
        protected the oldest entry goes anyway (the hard cap wins, loud log)."""
        self.memories.append(memory)
        if len(self.memories) <= NPC_MEMORY_CAP:
            return
        candidates = [
            i for i, m in enumerate(self.memories) if m.believed and m.importance < 5
        ]
        if not candidates:
            log.warning(
                "NPC '%s': all %d memories prune-protected — dropping the oldest anyway",
                self.name, len(self.memories),
            )
            self.memories.pop(0)
            return
        victim = min(candidates, key=lambda i: (self.memories[i].importance, i))
        self.memories.pop(victim)

    def add_agenda_step(self, step: AgendaStep) -> None:
        """Append an offscreen agenda step (ADR 049), pruning past :data:`AGENDA_LOG_CAP`:
        plain FIFO — the log is a timeline, the oldest step simply ages out."""
        self.agenda_log.append(step)
        while len(self.agenda_log) > AGENDA_LOG_CAP:
            self.agenda_log.pop(0)


def step_attitude(npc: Combatant, proposed: str) -> str:
    """Apply an attitude *proposal* to an NPC, clamped to ±1 step on :data:`ATTITUDE_SCALE`
    (ADR 044, golden rule #3): the extractor (LLM) only proposes, this code decides. An unknown
    proposed value is a no-op + log. An off-scale/empty *current* attitude (legacy free-text
    states) anchors at ``neutral`` so old states can still drift. Returns the (new) attitude."""
    key = (proposed or "").strip().lower()
    if key not in ATTITUDE_SCALE:
        if key:
            log.info(
                "NPC '%s': attitude proposal '%s' ignored (not on the scale)", npc.name, proposed
            )
        return npc.attitude
    current = npc.attitude.strip().lower()
    cur_idx = (
        ATTITUDE_SCALE.index(current)
        if current in ATTITUDE_SCALE
        else ATTITUDE_SCALE.index("neutral")
    )
    new_idx = cur_idx + max(-1, min(1, ATTITUDE_SCALE.index(key) - cur_idx))
    npc.attitude = ATTITUDE_SCALE[new_idx]
    return npc.attitude


# Allowed clock sizes (ADR 047) — the Blades-style small/medium/long fuse. Enforced at creation
# (`!uhr neu`); from_dict stays tolerant so a hand-edited state.json degrades instead of crashing.
CLOCK_SIZES = (4, 6, 8)


@dataclass
class Clock:
    """One consequence/progress clock (ADR 047) — code-owned like every hard fact (golden rule
    #3): the LLM only *requests* a tick via ``<<UHR id>>``, code validates and applies. ``visible``
    is schema-ready for hidden GM clocks; the UI deliberately ignores it for now (ADR 047 #4)."""

    id: str
    name: str
    size: int = 6
    filled: int = 0
    visible: bool = True

    @property
    def full(self) -> bool:
        return self.filled >= self.size

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "name": self.name, "size": self.size, "filled": self.filled}
        if not self.visible:
            d["visible"] = False
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Clock":
        size = int(d.get("size", 6) or 6)
        if size < 1:  # tolerate a hand-edited file; creation enforces CLOCK_SIZES
            size = 6
        return cls(
            id=str(d.get("id", "") or ""),
            name=str(d.get("name", "") or ""),
            size=size,
            filled=min(size, max(0, int(d.get("filled", 0) or 0))),
            visible=bool(d.get("visible", True)),
        )


def slugify_clock_id(name: str, *, fallback: str = "uhr") -> str:
    """A stable, marker-safe id from a clock/deadline name: lowercase, DE transliteration,
    non-alnum → ``-``. Never starts/ends with a separator (the glued-marker strip would peel a
    trailing ``-``/``_``, ADR 043's binding). Empty input degrades to ``fallback``. Deadlines
    (ADR 048) reuse this — same shape, same marker-safety habit."""
    text = name.strip().lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


@dataclass
class Deadline:
    """One in-game deadline (ADR 048) — code-owned like every hard fact (golden rule #3):
    created/removed by humans only (``!frist``), advanced against by the code-owned time
    counter. ``notified`` latches after the one-shot expiry note so it can never re-fire
    (persisted — a restart doesn't re-notify)."""

    id: str
    label: str
    due_minutes: int
    notified: bool = False

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "label": self.label, "due_minutes": self.due_minutes}
        if self.notified:
            d["notified"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Deadline":
        return cls(
            id=str(d.get("id", "") or ""),
            label=str(d.get("label", "") or ""),
            due_minutes=int(d.get("due_minutes", 0) or 0),
            notified=bool(d.get("notified", False)),
        )


@dataclass
class Quest:
    title: str
    status: str = "open"

    def to_dict(self) -> dict:
        return {"title": self.title, "status": self.status}

    @classmethod
    def from_dict(cls, d: dict) -> "Quest":
        return cls(title=d["title"], status=str(d.get("status", "open") or "open"))


@dataclass
class WorldState:
    """The mutable world state for one session (one voice channel). Serialises to ``state.json``."""

    system: str = ""
    session_id: str = ""
    characters: list[Combatant] = field(default_factory=list)
    npcs: list[Combatant] = field(default_factory=list)
    quests: list[Quest] = field(default_factory=list)
    location: str = ""
    # In-game time (ADR 048): the minutes counter since day 1, 00:00 is the model; the string
    # is the code-rendered human-readable mirror ("Tag 2, 14:30") — never parsed back, never
    # written by the LLM. Fresh campaigns start day 1, 08:00.
    time_ingame: str = ""
    time_minutes: int = DEFAULT_START_MINUTES
    recap: str = ""
    # Scene pointer into the loaded adventure compendium (Phase 10a, ADR 019) — the code-owned
    # "where are we in the plot" the prompt's scene card is selected by. Empty = no adventure.
    scene_id: str = ""
    # Scene-element flags (ADR 043): scene_id → element ids resolved there (used Gelegenheiten,
    # revealed Geheimnisse). Code-owned like scene_id (golden rule #3) — the LLM only *requests*
    # a flag via <<ERLEDIGT>>; validation lives in the runtime, this is dumb storage.
    scene_flags: dict[str, list[str]] = field(default_factory=dict)
    # Consequence clocks (ADR 047): code-owned pressure meters. The LLM only *requests* a tick
    # via <<UHR id>>; validation + the per-turn clamp live in the delivery pipeline.
    clocks: list[Clock] = field(default_factory=list)
    # Deadlines (ADR 048): human-created, expire against the code-owned time counter.
    deadlines: list[Deadline] = field(default_factory=list)

    # -- (de)serialisation ----------------------------------------------------------------

    def to_dict(self) -> dict:
        d = {
            "session_id": self.session_id,
            "system": self.system,
            "characters": [c.to_dict() for c in self.characters],
            "npcs": [n.to_dict() for n in self.npcs],
            "quests": [q.to_dict() for q in self.quests],
            "location": self.location,
            "time_ingame": self.time_ingame,
            "time_minutes": self.time_minutes,
            "recap": self.recap,
            "scene_id": self.scene_id,
        }
        if self.scene_flags:  # omit-when-empty, like the Combatant extras
            d["scene_flags"] = {k: list(v) for k, v in self.scene_flags.items() if v}
        if self.clocks:  # omit-when-empty (ADR 047) — an old state.json shape stays untouched
            d["clocks"] = [c.to_dict() for c in self.clocks]
        if self.deadlines:  # omit-when-empty (ADR 048)
            d["deadlines"] = [dl.to_dict() for dl in self.deadlines]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WorldState":
        # Time migration (ADR 048 #2): a pre-048 state.json has no counter — start it at the
        # default (day 1, 08:00) and say so. A legacy free-text time_ingame is NOT parsed
        # (prose); it stays visible until the first advance re-renders it from the counter.
        raw_minutes = d.get("time_minutes")
        if raw_minutes is None:
            time_minutes = DEFAULT_START_MINUTES
            legacy = str(d.get("time_ingame", "") or "")
            if d:  # only a real loaded state is a migration; the {}-default path stays quiet
                log.info(
                    "state migration (ADR 048): no time_minutes — starting the clock at %s%s",
                    render_time_de(time_minutes),
                    f" (legacy time_ingame {legacy!r} left as display until the first advance)"
                    if legacy else "",
                )
        else:
            time_minutes = max(0, int(raw_minutes or 0))
        return cls(
            system=str(d.get("system", "") or ""),
            session_id=str(d.get("session_id", "") or ""),
            characters=[Combatant.from_dict(c) for c in d.get("characters", []) or []],
            npcs=[Combatant.from_dict(n, is_npc=True) for n in d.get("npcs", []) or []],
            quests=[Quest.from_dict(q) for q in d.get("quests", []) or []],
            location=str(d.get("location", "") or ""),
            time_ingame=str(d.get("time_ingame", "") or ""),
            time_minutes=time_minutes,
            recap=str(d.get("recap", "") or ""),
            scene_id=str(d.get("scene_id", "") or ""),
            scene_flags={
                str(k): [str(x) for x in (v or [])]
                for k, v in (d.get("scene_flags") or {}).items()
            },
            clocks=[Clock.from_dict(c) for c in d.get("clocks", []) or []],
            deadlines=[Deadline.from_dict(dl) for dl in d.get("deadlines", []) or []],
        )

    @classmethod
    def load(cls, path: Path) -> "WorldState | None":
        """Load state from ``path``; ``None`` if it doesn't exist yet (the caller then seeds it)."""
        if not path.is_file():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        """Write atomically (temp + ``os.replace``) so a crash mid-write can't corrupt the file —
        the gate is 'an HP change survives a restart', so this file must always be readable."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)  # atomic on Windows + POSIX
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @classmethod
    def seed_from_store(
        cls, store: "CharacterStore", *, system: str = "", session_id: str = ""
    ) -> "WorldState":
        """Build a fresh state from the read-only sheet (the once-only transfer, ADR 004): copy each
        character's current/max wounds, conditions and inventory into the mutable layer."""
        chars = [
            Combatant(
                name=c.name,
                wounds=c.wounds if c.wounds is not None else (c.max_wounds or 0),
                max_wounds=c.max_wounds if c.max_wounds is not None else (c.wounds or 0),
                conditions=list(c.conditions),
                inventory=list(c.inventory),
            )
            for c in store.characters()
        ]
        return cls(system=system, session_id=session_id, characters=chars)

    # -- lookup ---------------------------------------------------------------------------

    def find(self, name: str | None) -> Combatant | None:
        """Find a combatant (character first, then NPC) by name, case-insensitively."""
        if not name:
            return None
        key = name.strip().lower()
        for c in self.characters:
            if c.name.lower() == key:
                return c
        for n in self.npcs:
            if n.name.lower() == key:
                return n
        return None

    # -- scene-element flags (ADR 043) ------------------------------------------------------

    def resolved_ids(self, scene_id: str) -> list[str]:
        """The element ids flagged resolved for ``scene_id`` (order of resolution)."""
        return list(self.scene_flags.get(scene_id, []))

    def mark_resolved(self, scene_id: str, element_id: str) -> bool:
        """Flag ``element_id`` resolved for ``scene_id``; False if it already was (idempotent)."""
        flags = self.scene_flags.setdefault(scene_id, [])
        if element_id in flags:
            return False
        flags.append(element_id)
        return True

    def mark_open(self, scene_id: str, element_id: str) -> bool:
        """Un-flag ``element_id`` for ``scene_id``; False if it wasn't set. Drops an emptied
        scene key so ``to_dict``'s omit-when-empty stays clean."""
        flags = self.scene_flags.get(scene_id, [])
        if element_id not in flags:
            return False
        flags.remove(element_id)
        if not flags:
            self.scene_flags.pop(scene_id, None)
        return True

    # -- consequence clocks (ADR 047) --------------------------------------------------------

    def find_clock(self, clock_id: str | None) -> Clock | None:
        """Find a clock by id, case-insensitively (ids are lowercase slugs; a hand-edited
        state.json may differ in case — tolerate it like ``find`` does for names)."""
        if not clock_id:
            return None
        key = clock_id.strip().lower()
        return next((c for c in self.clocks if c.id.lower() == key), None)

    def add_clock(self, name: str, size: int) -> Clock:
        """Create a clock with a slug id derived from ``name`` (deduped with a numeric suffix).
        ``size`` is the caller's job to validate against :data:`CLOCK_SIZES` (the command does)."""
        base = slugify_clock_id(name)
        cid, n = base, 1
        while self.find_clock(cid) is not None:
            n += 1
            cid = f"{base}-{n}"
        clock = Clock(id=cid, name=name.strip(), size=size)
        self.clocks.append(clock)
        return clock

    def remove_clock(self, clock_id: str) -> Clock | None:
        """Remove a clock by id; returns it, or ``None`` if unknown."""
        clock = self.find_clock(clock_id)
        if clock is not None:
            self.clocks.remove(clock)
        return clock

    def tick_clock(self, clock_id: str) -> Clock | None:
        """Advance a clock one segment (never past ``size``). Returns the clock, or ``None``
        for an unknown id **or an already-full clock** — a full clock's consequence is due,
        not tickable. The caller decides what a fresh fill triggers (the GM-note injection)."""
        clock = self.find_clock(clock_id)
        if clock is None or clock.full:
            return None
        clock.filled += 1
        return clock

    def untick_clock(self, clock_id: str) -> Clock | None:
        """Take one segment back (never below 0). Returns the clock, or ``None`` if unknown."""
        clock = self.find_clock(clock_id)
        if clock is None:
            return None
        clock.filled = max(0, clock.filled - 1)
        return clock

    # -- in-game time + deadlines (ADR 048) --------------------------------------------------

    def advance_time(self, minutes: int) -> list[Deadline]:
        """Advance the code-owned clock by ``minutes`` (≤0 is a no-op) and re-render the
        human-readable mirror. Returns the deadlines that *newly* expired on this advance
        (crossed ``due_minutes`` and were not yet notified) — each is latched ``notified``
        here, so the expiry note fires exactly once (ADR 048 #8). The caller (runtime)
        queues the [Regie] notes and persists."""
        if minutes <= 0:
            return []
        self.time_minutes += minutes
        self.time_ingame = render_time_de(self.time_minutes)
        expired: list[Deadline] = []
        for dl in self.deadlines:
            if not dl.notified and dl.due_minutes <= self.time_minutes:
                dl.notified = True
                expired.append(dl)
        return expired

    def find_deadline(self, deadline_id: str | None) -> Deadline | None:
        """Find a deadline by id, case-insensitively (like :meth:`find_clock`)."""
        if not deadline_id:
            return None
        key = deadline_id.strip().lower()
        return next((dl for dl in self.deadlines if dl.id.lower() == key), None)

    def add_deadline(self, label: str, in_minutes: int) -> Deadline:
        """Create a deadline ``in_minutes`` from now, with a slug id derived from the label
        (deduped with a numeric suffix, the clock-id scheme). Human-only (ADR 048 #7)."""
        base = slugify_clock_id(label, fallback="frist")
        did, n = base, 1
        while self.find_deadline(did) is not None:
            n += 1
            did = f"{base}-{n}"
        deadline = Deadline(id=did, label=label.strip(),
                            due_minutes=self.time_minutes + max(0, in_minutes))
        self.deadlines.append(deadline)
        return deadline

    def remove_deadline(self, deadline_id: str) -> Deadline | None:
        """Remove a deadline by id; returns it, or ``None`` if unknown."""
        deadline = self.find_deadline(deadline_id)
        if deadline is not None:
            self.deadlines.remove(deadline)
        return deadline

    # -- deterministic advancement (golden rule #3) ---------------------------------------

    def apply_damage(
        self, name: str, amount: int, *, downed_condition: str = DOWNED_CONDITION
    ) -> Combatant | None:
        """Subtract ``amount`` wounds (already soaked — the engine did the math), clamped at 0. At 0
        the combatant gains the downed condition. Returns the updated combatant, or ``None`` if the
        name is unknown."""
        c = self.find(name)
        if c is None:
            return None
        c.wounds = max(0, c.wounds - max(0, amount))
        if c.wounds == 0 and downed_condition and downed_condition not in c.conditions:
            c.conditions.append(downed_condition)
        return c

    def heal(
        self, name: str, amount: int, *, downed_condition: str = DOWNED_CONDITION
    ) -> Combatant | None:
        """Restore ``amount`` wounds, clamped at ``max_wounds``. Rising above 0 clears the downed
        condition. Returns the updated combatant, or ``None`` if unknown."""
        c = self.find(name)
        if c is None:
            return None
        c.wounds = min(c.max_wounds, c.wounds + max(0, amount))
        if c.wounds > 0 and downed_condition in c.conditions:
            c.conditions.remove(downed_condition)
        return c

    def add_condition(self, name: str, condition: str) -> Combatant | None:
        c = self.find(name)
        if c is not None and condition and condition not in c.conditions:
            c.conditions.append(condition)
        return c

    def remove_condition(self, name: str, condition: str) -> Combatant | None:
        c = self.find(name)
        if c is not None and condition in c.conditions:
            c.conditions.remove(condition)
        return c

    def add_or_update_npc(
        self,
        name: str,
        *,
        wounds: int | None = None,
        max_wounds: int | None = None,
        toughness_bonus: int = 0,
        armour: int = 0,
        attitude: str = "hostile",
        faction: str = "",
        goal: str = "",
    ) -> Combatant:
        """Register an NPC (an enemy the party can damage) or update an existing one."""
        existing = next((n for n in self.npcs if n.name.lower() == name.strip().lower()), None)
        max_w = max_wounds if max_wounds is not None else (wounds if wounds is not None else 10)
        if existing is None:
            npc = Combatant(
                name=name.strip(),
                wounds=wounds if wounds is not None else max_w,
                max_wounds=max_w,
                toughness_bonus=toughness_bonus,
                armour=armour,
                is_npc=True,
                attitude=attitude,
                faction=faction,
                goal=goal,
            )
            self.npcs.append(npc)
            return npc
        if wounds is not None:
            existing.wounds = wounds
        if max_wounds is not None:
            existing.max_wounds = max_wounds
        if toughness_bonus:
            existing.toughness_bonus = toughness_bonus
        if armour:
            existing.armour = armour
        if attitude:
            existing.attitude = attitude
        if faction:
            existing.faction = faction
        if goal:
            existing.goal = goal
        return existing

    # -- psyker / Warp Charge (ADR 022) ---------------------------------------------------

    def set_warp_charge(self, name: str, value: int) -> Combatant | None:
        """Set a combatant's Warp Charge to ``value`` (clamped ≥ 0). Returns the combatant or
        ``None`` if unknown. The engine computes the new total; this only stores it."""
        c = self.find(name)
        if c is not None:
            c.warp_charge = max(0, int(value))
        return c

    def reset_warp_charge(self, name: str) -> Combatant | None:
        """Reset Warp Charge to 0 and drop all Sustained powers — what Perils of the Warp does."""
        c = self.find(name)
        if c is not None:
            c.warp_charge = 0
            c.sustained_powers = []
        return c

    def sustain_power(self, name: str, power: str) -> Combatant | None:
        c = self.find(name)
        if c is not None and power and power not in c.sustained_powers:
            c.sustained_powers.append(power)
        return c

    def set_location(self, location: str) -> None:
        self.location = location.strip()

    def add_quest(self, title: str, *, status: str = "open") -> Quest:
        existing = next((q for q in self.quests if q.title.lower() == title.strip().lower()), None)
        if existing is not None:
            existing.status = status
            return existing
        q = Quest(title=title.strip(), status=status)
        self.quests.append(q)
        return q

    def set_quest_status(self, title: str, status: str) -> Quest | None:
        q = next((q for q in self.quests if q.title.lower() == title.strip().lower()), None)
        if q is not None:
            q.status = status
        return q

    def set_recap(self, text: str) -> None:
        self.recap = text.strip()


def _combatant_line_de(c: Combatant) -> str:
    """'Seskin 8/11 (verwundet)' / 'Mortn 0/9 (kampfunfähig)' — compact per-combatant status."""
    tags: list[str] = []
    if c.wounds <= 0:
        pass  # the downed condition already conveys it; avoid "(0/9, kampfunfähig)" noise
    elif c.wounds < c.max_wounds:
        tags.append("verwundet")
    tags.extend(c.conditions)
    if c.warp_charge or c.sustained_powers:
        warp = f"Warp {c.warp_charge}"
        if c.sustained_powers:
            warp += f", hält: {', '.join(c.sustained_powers)}"
        tags.append(warp)
    suffix = f" ({', '.join(tags)})" if tags else ""
    head = f"{c.name} {c.wounds}/{c.max_wounds}"
    if c.is_npc and c.attitude:
        head = f"{c.name} [{c.attitude}] {c.wounds}/{c.max_wounds}"
    return head + suffix


def _agenda_line_de(n: Combatant) -> str:
    """'Vex → will die Ware außer Reichweite schaffen (zuletzt: …)' — one compact line per
    agenda NPC (ADR 049) for the world-state block."""
    line = f"{n.name} → {n.goal}"
    if n.agenda_log:
        line += f" (zuletzt: {n.agenda_log[-1].text})"
    return line


def clock_segments(clock: Clock) -> str:
    """'◉◉◉○○○' — the filled/empty segment string for panel + command replies (ADR 047)."""
    return "◉" * min(clock.filled, clock.size) + "○" * max(0, clock.size - clock.filled)


def clock_line_de(clock: Clock) -> str:
    """'[arbites] Arbites-Ermittlung 3/6' (+ ' — VOLL') — the compact prompt rendering: the id in
    brackets so the model can cite it in ``<<UHR id>>``, exactly like scene-element ids (ADR 043)."""
    line = f"[{clock.id}] {clock.name} {clock.filled}/{clock.size}"
    return f"{line} — VOLL" if clock.full else line


def clock_full_note_de(clock: Clock) -> str:
    """The one-shot ``[Regie]`` directive queued when a clock fills (ADR 047). The clock name is
    wrapped in „…“ — ``discard_gm_notes(containing='„<name>“')`` relies on exactly this framing
    to retract the note when ``!uhr zurück`` undoes an accidental fill."""
    return (
        f"Die Uhr „{clock.name}“ ist voll — die angekündigte Konsequenz tritt JETZT ein. "
        "Erzähle sie in deinem nächsten Beitrag als Ereignis in der Szene."
    )


def clocks_panel_de(clocks: list[Clock]) -> str:
    """The Discord clock panel body (ADR 047): one line per clock, filled/empty segments.
    Visible-to-all first cut — ``visible`` is deliberately ignored here (ADR 047 #4)."""
    lines = ["⏱ **Uhren**"]
    for c in clocks:
        head = "⌛" if c.full else "⏱"
        line = f"{head} {clock_segments(c)} **{c.name}** (`{c.id}`) {c.filled}/{c.size}"
        if c.full:
            line += " — **VOLL**"
        lines.append(line)
    return "\n".join(lines)


def pressure_panel_de(state: WorldState) -> str:
    """The combined pressure-panel body (ADR 048 #11): current time + day phase on top, then
    open deadlines, then the clocks — one edit-in-place panel instead of a second one. The
    caller shows it whenever clocks OR deadlines exist."""
    lines = [f"🕐 **{render_time_de(state.time_minutes)}** ({day_phase_de(state.time_minutes)})"]
    for dl in state.deadlines:
        line = f"⏳ **{dl.label}** (`{dl.id}`) — {remaining_de(dl.due_minutes, state.time_minutes)}"
        lines.append(line)
    if state.clocks:
        lines.append(clocks_panel_de(state.clocks))
    return "\n".join(lines)


def world_state_summary_de(state: WorldState) -> str:
    """A compact, *structured* German block for the prompt (docs/conventions.md: 'state as structured data,
    don't boil it into prose'). Only non-empty sections appear. Empty state → ''."""
    lines: list[str] = []
    if state.location:
        lines.append(f"Ort: {state.location}")
    # Time renders from the counter (ADR 048) — always present, phase included so the DM can
    # play it (nachts ist der Wirt nicht da). The legacy time_ingame string is display-only.
    lines.append(f"Zeit: {render_time_phase_de(state.time_minutes)}")
    if state.deadlines:
        lines.append("Fristen: " + "; ".join(
            deadline_line_de(dl.id, dl.label, dl.due_minutes, state.time_minutes)
            for dl in state.deadlines
        ))
    if state.characters:
        lines.append("Gruppe: " + "; ".join(_combatant_line_de(c) for c in state.characters))
    living_npcs = [n for n in state.npcs if n.wounds > 0]
    if living_npcs:
        lines.append("NSCs in der Szene: " + "; ".join(_combatant_line_de(n) for n in living_npcs))
    # Agenda NPCs (ADR 049): one compact line each, so offscreen movement is felt even when the
    # NPC is elsewhere — the DM surfaces it as rumours and traces, never as hard facts.
    agenda_npcs = [n for n in living_npcs if n.goal]
    if agenda_npcs:
        lines.append(
            "Agenden (diese NSCs handeln offscreen weiter — deute ihre Bewegungen über "
            "Gerüchte und Spuren an, wenn sie nicht anwesend sind): "
            + "; ".join(_agenda_line_de(n) for n in agenda_npcs)
        )
    open_quests = [q.title for q in state.quests if q.status == "open"]
    if open_quests:
        lines.append("Offene Aufträge: " + "; ".join(open_quests))
    if state.clocks:  # ADR 047 — visible-to-all first cut: every clock rides in the prompt
        lines.append("Uhren (Druck/Fortschritt): " + "; ".join(clock_line_de(c) for c in state.clocks))
    if not lines:
        return ""
    return "## Weltzustand (harte Fakten — verlass dich darauf, erfinde keine abweichenden Werte)\n" + "\n".join(lines)
