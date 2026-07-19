"""NPC memory — extraction, attitude drift, faction gossip (ADR 044).

NPCs remember what was discussed with them, like a human GM would: per-NPC gists (+ verbatim
key quotes for promises/lies/threats), injected into the DM prompt while the NPC is in the
scene. The split follows golden rule #3 exactly like the dice flow: the **LLM extracts and
proposes** (memories are a narrative layer, like the recap), **code validates and applies**
everything hard — the attitude proposal is clamped to ±1 step per scene
(:func:`~trpg_agent.memory.state.step_attitude`), revealed lies are flipped by code, and gossip is
deterministic propagation, not another LLM call.

Pure functions + one LLM-call wrapper with an injected OllamaClient (testable like
``rules/combat.py`` — no Discord, no ``SessionRuntime``). The trigger seam (scene change /
``wrap up``) and persistence live in :mod:`dmbot.runtime`.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .chekhov import CHEKHOV_SCHEMA
from .state import ATTITUDE_SCALE, AgendaStep, Combatant, NpcMemory, WorldState, step_attitude

if TYPE_CHECKING:
    from ..llm.client import OllamaClient
    from ..rag.adventure import AdventureNpc

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "npc_memory_extract_zh.md"

# Hard render cap per gist (chars) — requested from the extractor, enforced again at apply AND
# render time so the prompt block stays bounded no matter what the model returned.
GIST_MAX_CHARS = 200

# Gossip (ADR 044): only direct memories at least this important spread through a faction.
GOSSIP_MIN_IMPORTANCE = 4

# Agenda (ADR 049): how many recent agenda_log steps ride in the extractor *input* (context for
# a plausible next step) and in the *prompt block* of a present agenda NPC.
AGENDA_INPUT_STEPS = 2
AGENDA_RENDER_STEPS = 3

# Chinese labels for the prompt block; stored tokens stay English (code).
_ATTITUDE_ZH = {
    "hostile": "敌对",
    "wary": "警惕",
    "neutral": "中立",
    "friendly": "友善",
    "loyal": "忠诚",
}

# Ollama structured-output schema (like the roll router, ADR 014): forces valid JSON so the
# tolerant parse below is a belt, not the whole trousers.
EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "npcs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "memories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "about": {"type": "array", "items": {"type": "string"}},
                                "gist": {"type": "string"},
                                "quote": {"type": "string"},
                                "importance": {"type": "integer"},
                            },
                            "required": ["about", "gist"],
                        },
                    },
                    "attitude_proposal": {"type": "string"},
                    "revealed_lies": {"type": "array", "items": {"type": "integer"}},
                    "agenda_step": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["npcs"],
}

# Wrap-up variant (ADR 050): the same call additionally maintains the Chekhov list — the
# `chekhov` section is required so the model always answers the question (empty lists are fine).
EXTRACT_SCHEMA_CHEKHOV: dict = copy.deepcopy(EXTRACT_SCHEMA)
EXTRACT_SCHEMA_CHEKHOV["properties"]["chekhov"] = CHEKHOV_SCHEMA
EXTRACT_SCHEMA_CHEKHOV["required"] = ["npcs", "chekhov"]

_CHEKHOV_PROMPT_PATH = _PROMPT_PATH.parent / "chekhov_extract_zh.md"


def attitude_zh(attitude: str) -> str:
    """German label for a stored attitude token; an off-scale/legacy value renders verbatim."""
    key = (attitude or "").strip().lower()
    return _ATTITUDE_ZH.get(key, attitude or "未知")


# -- parsing (tolerant) ---------------------------------------------------------------------


def parse_extraction(raw: str) -> dict | None:
    """Parse the extractor's JSON answer, tolerantly: markdown fences are stripped, anything
    that doesn't decode to ``{"npcs": [...]}`` returns ``None`` (the caller retries once, then
    skips — an extraction must never block the scene change)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("npcs"), list):
        return None
    return data


# -- extractor input ------------------------------------------------------------------------


def build_extract_user(
    turns: list[dict[str, str]], npcs: list[Combatant], scene_id: str,
    now_ingame: str = "",
) -> str:
    """Render the elapsed scene for the extractor: the present NPCs (with attitude + their
    *numbered* existing memories, so ``revealed_lies`` can reference them and known facts
    aren't re-recorded) followed by the transcript (labelled like the recap input).

    Agenda NPCs (ADR 049) additionally carry their goal + the last few agenda steps, and the
    current in-game time rides along — so a proposed ``agenda_step`` stays plausible against
    the elapsed time."""
    lines: list[str] = []
    if scene_id:
        lines.append(f"场景：{scene_id}")
    if now_ingame:
        lines.append(f"当前游戏时间：{now_ingame}")
    lines.append("在场 NPC：")
    for npc in npcs:
        lines.append(f"- {npc.name} （态度：{attitude_zh(npc.attitude)}）")
        if npc.goal:
            lines.append(f"  目标：{npc.goal}")
            for step in npc.agenda_log[-AGENDA_INPUT_STEPS:]:
                ts = f" ({step.ts_ingame})" if step.ts_ingame else ""
                lines.append(f"  先前步骤{ts}：{step.text}")
        for i, m in enumerate(npc.memories):
            quote = f" Zitat: „{m.quote}“" if m.quote else ""
            lie = " [谎言已揭露]" if not m.believed else ""
            lines.append(f"  [{i}] {m.gist}{quote}{lie}")
    lines.append("")
    lines.append("场景对话记录：")
    for msg in turns:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        speaker = "主持人" if msg.get("role") == "assistant" else "玩家"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


# -- application (code-owned, golden rule #3) -------------------------------------------------


def _truncate_gist(gist: str) -> str:
    gist = " ".join(gist.split())
    if len(gist) <= GIST_MAX_CHARS:
        return gist
    return gist[: GIST_MAX_CHARS - 1].rstrip() + "…"


def _npc_only_find(state: WorldState, name: str) -> Combatant | None:
    """Find an NPC by name — never a player character (an extractor that hallucinates a PC name
    must not attach memories to the party)."""
    key = (name or "").strip().lower()
    if not key:
        return None
    return next((n for n in state.npcs if n.name.lower() == key), None)


def _liar_zh(about: list[str]) -> str:
    """Who lied, for the flip entry's gist: the first ``pc:``-scoped name, else '队伍'."""
    for a in about:
        a = a.strip()
        if a.lower().startswith("pc:"):
            return a[3:].strip()
    return "队伍"


def apply_extraction(
    state: WorldState,
    payload: dict,
    *,
    scene_id: str,
    now: str = "",
    now_ingame: str = "",
    statblock: "Callable[[str], AdventureNpc | None] | None" = None,
) -> list[tuple[Combatant, NpcMemory]]:
    """Apply one scene's extraction to the world state — all the *hard* effects happen here, in
    code (golden rule #3):

    - **Lie flips first** (``revealed_lies`` indexes the memory numbering the extractor was
      shown, i.e. the pre-existing entries): ``believed = False``, a new importance-5 entry
      records the reveal, and the attitude steps once toward ``hostile`` — in addition to the
      normal proposal clamp below.
    - **New memories** are deduped against the NPC's existing gists (the extraction window is
      approximate around auto-compaction — a duplicate window must not duplicate entries),
      gist-truncated, clamped to importance 1–5 and appended via the capped ``add_memory``.
    - **Attitude proposal** is clamped to ±1 step per scene by :func:`step_attitude`.
    - **Agenda step** (ADR 049): at most **one** per NPC per extraction (duplicate payload
      entries are dropped), only for a *living* NPC with a non-empty ``goal`` — a step for a
      goalless NPC or a PC is discarded. Narrative-only: appended to the capped
      ``agenda_log``, never a hard mutation.

    An NPC named by the extractor but not yet registered is added (statblock values when the
    adventure knows it, attitude ``neutral``) — memories need a place to live. A name matching
    a *player character* is skipped. Returns the newly added **direct** entries as
    ``(npc, memory)`` pairs — the input :func:`propagate_gossip` consumes.
    """
    new_entries: list[tuple[Combatant, NpcMemory]] = []
    agenda_stepped: set[int] = set()  # id(npc) → already got its one step this extraction
    for entry in payload.get("npcs", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        if not name:
            continue
        npc = _npc_only_find(state, name)
        if npc is None:
            if state.find(name) is not None:  # a PC — never attach NPC memories to the party
                log.info("NPC-memory: '%s' is a player character — skipped", name)
                continue
            block = statblock(name) if statblock is not None else None
            npc = state.add_or_update_npc(
                name,
                wounds=block.wounds if block else None,
                toughness_bonus=block.toughness_bonus if block else 0,
                armour=block.armour if block else 0,
                attitude="neutral",
                faction=block.faction if block else "",
                goal=block.goal_de if block else "",
            )
            log.info("NPC-memory: registered '%s' (first memory)", npc.name)
        elif (not npc.faction or not npc.goal) and statblock is not None:
            block = statblock(name)
            if block is not None:  # backfill authored faction/goal onto older states
                if not npc.faction and block.faction:
                    npc.faction = block.faction
                if not npc.goal and block.goal_de:
                    npc.goal = block.goal_de

        # 1) Lie flips (code, not LLM) — indexes refer to the pre-existing entries the extractor
        #    was shown, so they run before anything is appended.
        pre_existing = list(npc.memories)
        for idx in entry.get("revealed_lies", []) or []:
            if not isinstance(idx, int) or not 0 <= idx < len(pre_existing):
                log.info("NPC-memory: '%s' revealed_lies index %r invalid — skipped", npc.name, idx)
                continue
            lied = pre_existing[idx]
            if not lied.believed:
                continue  # already known as a lie — idempotent
            lied.believed = False
            npc.add_memory(
                NpcMemory(
                    about=list(lied.about),
                    gist=_truncate_gist(
                        f"被{_liar_zh(lied.about)}欺骗 — „{lied.gist}“ 是谎言。"
                    ),
                    importance=5,
                    scene=scene_id,
                    ts=now,
                )
            )
            cur = npc.attitude.strip().lower()
            cur_idx = ATTITUDE_SCALE.index(cur) if cur in ATTITUDE_SCALE else ATTITUDE_SCALE.index("neutral")
            step_attitude(npc, ATTITUDE_SCALE[max(0, cur_idx - 1)])
            log.info("NPC-memory: '%s' — 谎言揭露（当前态度 %s）", npc.name, npc.attitude)

        # 2) New memories, deduped on gist.
        seen_gists = {m.gist.strip().casefold() for m in npc.memories}
        for m in entry.get("memories", []) or []:
            if not isinstance(m, dict):
                continue
            gist = _truncate_gist(str(m.get("gist", "") or ""))
            if not gist or gist.strip().casefold() in seen_gists:
                continue
            about = [str(a).strip() for a in m.get("about", []) or [] if str(a).strip()]
            try:
                importance = min(5, max(1, int(m.get("importance", 3))))
            except (TypeError, ValueError):
                importance = 3
            memory = NpcMemory(
                about=about or ["party"],
                gist=gist,
                quote=str(m.get("quote", "") or "").strip(),
                importance=importance,
                scene=scene_id,
                ts=now,
            )
            npc.add_memory(memory)
            seen_gists.add(gist.strip().casefold())
            new_entries.append((npc, memory))

        # 3) Attitude proposal — validated + clamped by code (never written from free text).
        proposal = str(entry.get("attitude_proposal", "") or "")
        if proposal:
            before = npc.attitude
            after = step_attitude(npc, proposal)
            if after != before:
                log.info("NPC-memory: '%s' 态度 %s → %s（提议：%s）",
                         npc.name, before or "—", after, proposal)

        # 4) Agenda step (ADR 049) — narrative log entry only, max one per NPC per extraction;
        #    a step for a goalless or dead NPC is the extractor overreaching → discarded.
        step_text = _truncate_gist(str(entry.get("agenda_step", "") or ""))
        if step_text:
            if not npc.goal:
                log.info("NPC-memory: agenda step for '%s' without a goal — discarded", npc.name)
            elif npc.wounds <= 0:
                log.info("NPC-memory: agenda step for dead '%s' — discarded", npc.name)
            elif id(npc) in agenda_stepped:
                log.info("NPC-memory: duplicate agenda step for '%s' — discarded", npc.name)
            else:
                npc.add_agenda_step(AgendaStep(ts_ingame=now_ingame, text=step_text))
                agenda_stepped.add(id(npc))
                log.info("NPC-memory: '%s' 议程步骤：%s", npc.name, step_text)
    return new_entries


def propagate_gossip(
    state: WorldState, new_entries: list[tuple[Combatant, NpcMemory]]
) -> int:
    """Spread a scene's important news through factions (ADR 044) — deterministic code, no LLM:
    every **new direct** memory with importance ≥ 4 is copied to all *other* NPCs sharing the
    source's non-empty ``faction`` — as hearsay (``source: "gossip"``, no quote, importance −1).
    No gossip-of-gossip (only direct entries arrive here), no duplicates (same gist at the
    recipient → skip). Returns the number of copies planted."""
    planted = 0
    for src, mem in new_entries:
        if mem.source != "direct" or mem.importance < GOSSIP_MIN_IMPORTANCE:
            continue
        faction = src.faction.strip().lower()
        if not faction:
            continue
        for other in state.npcs:
            if other is src or other.faction.strip().lower() != faction:
                continue
            if any(m.gist.strip().casefold() == mem.gist.strip().casefold() for m in other.memories):
                continue
            other.add_memory(
                NpcMemory(
                    about=list(mem.about),
                    gist=mem.gist,
                    importance=mem.importance - 1,
                    source="gossip",
                    scene=mem.scene,
                    ts=mem.ts,
                )
            )
            planted += 1
    if planted:
        log.info("NPC-memory: 传播 %d 条传闻", planted)
    return planted


# -- prompt injection -----------------------------------------------------------------------


def select_top_memories(npc: Combatant, top_k: int) -> list[NpcMemory]:
    """The entries worth the prompt budget: every ``believed: False`` entry is always included
    (the NPC *knows* it was lied to), the remaining slots go by importance, then recency."""
    flipped = [m for m in npc.memories if not m.believed]
    rest = [m for m in npc.memories if m.believed]
    order = {id(m): i for i, m in enumerate(npc.memories)}  # append order = recency
    rest.sort(key=lambda m: (-m.importance, -order[id(m)]))
    return flipped + rest[: max(0, top_k - len(flipped))]


def npc_memory_block_zh(npcs: list[Combatant], *, top_k: int = 6) -> str:
    """The compact Chinese prompt block: one ``[NPC 记忆：…]`` header per scene NPC with
    its top-K entries. Gossip renders as "传闻" (the DM keeps it vague), flipped lies as
    "谎言已揭露". An agenda NPC (ADR 049) additionally carries its goal + the last
    few 幕后 steps (and renders even without memories). Gists are hard-truncated; NPCs
    without memories or a goal are skipped; nothing to render → ''."""
    blocks: list[str] = []
    for npc in npcs:
        if not npc.memories and not npc.goal:
            continue
        lines = [f"[NPC 记忆：{npc.name} — 态度：{attitude_zh(npc.attitude)}]"]
        if npc.goal:
            lines.append(f"目标：{npc.goal}")
            for step in npc.agenda_log[-AGENDA_RENDER_STEPS:]:
                ts = f", {step.ts_ingame}" if step.ts_ingame else ""
                lines.append(f"-（幕后{ts}）{_truncate_gist(step.text)}")
        for m in select_top_memories(npc, top_k):
            if not m.believed:
                tag = "（谎言已揭露）"
            elif m.source == "gossip":
                tag = "（传闻）"
            elif m.importance >= 4:
                tag = "（重要）"
            else:
                tag = ""
            quote = f" Zitat: „{m.quote}“" if m.quote and m.source != "gossip" else ""
            lines.append(f"- {tag}{_truncate_gist(m.gist)}{quote}")
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return (
        "## NPC 记忆（这些 NPC 在之前对话中了解的信息——在对话中使用；"
        "传闻仅模糊地二手转述；幕后步骤是 NPC 在场景之间为实现目标所做的事情）\n"
        + "\n".join(blocks)
    )


# -- the LLM-call wrapper ---------------------------------------------------------------------


async def request_extraction(
    client: "OllamaClient",
    *,
    turns: list[dict[str, str]],
    npcs: list[Combatant],
    scene_id: str,
    now_ingame: str = "",
    chekhov_section: str = "",
    prompt_path: Path = _PROMPT_PATH,
    chekhov_prompt_path: Path = _CHEKHOV_PROMPT_PATH,
) -> dict | None:
    """One structured-JSON extraction call for an elapsed scene (injected client, like the roll
    router): low temperature, neutralised repeat penalty, schema-constrained. Tolerant parse
    with ONE retry; then skip + warn — never raises parse trouble at the caller (the scene
    change must not block). Transport errors do propagate; the runtime wrapper catches them.

    A non-empty ``chekhov_section`` (the wrap-up call, ADR 050) switches to the extended
    schema, appends the Chekhov rules to the system prompt and the section (open threads +
    earlier-session context) to the user message — one call, never two."""
    system = prompt_path.read_text(encoding="utf-8").strip()
    user = build_extract_user(turns, npcs, scene_id, now_ingame=now_ingame)
    schema = EXTRACT_SCHEMA
    num_predict = 800
    if chekhov_section:
        system += "\n\n" + chekhov_prompt_path.read_text(encoding="utf-8").strip()
        user += "\n" + chekhov_section
        schema = EXTRACT_SCHEMA_CHEKHOV
        num_predict = 1000  # headroom for the extra chekhov section in the answer
    for attempt in (1, 2):
        raw = await client.chat(
            system,
            [{"role": "user", "content": user}],
            options={"temperature": 0.2, "num_predict": num_predict, "repeat_penalty": 1.0},
            format=schema,
        )
        payload = parse_extraction(raw)
        if payload is not None:
            return payload
        log.warning("NPC-memory extraction: unparseable JSON (attempt %d/2)", attempt)
    return None
