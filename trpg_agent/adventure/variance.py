"""Roguelike 变体系统 — 固定模组骨架 + 随机血肉。

设计原则：
- 核心剧情不变（A 必须到 B 再到 C），但路径上的细节随机
- 线索位置可变（同一个线索可能出现在不同房间）
- NPC 性格随机（同一个人物每次态度/秘密不同）
- 遭遇随机（场景间移动可能触发随机事件）
- 氛围描述随机（同一场景每次文字不同）
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


# ── 随机遭遇 ────────────────────────────────────────


@dataclass
class RandomEncounter:
    """一条随机遭遇——场景切换时可能触发的事件。"""

    id: str
    weight: int = 1                        # 权重（越高越容易出现）
    description: str = ""                  # DM 看到的描述
    san_check: str | None = None           # SAN 损失等级（如有）
    combat: dict | None = None             # 战斗配置（如有）
    npc_intro: str | None = None           # 引入的 NPC 名
    clue_reward: str | None = None         # 发现线索的描述

    @classmethod
    def from_dict(cls, d: dict) -> "RandomEncounter":
        return cls(
            id=str(d.get("id", "") or ""),
            weight=int(d.get("weight", 1) or 1),
            description=str(d.get("description", "") or ""),
            san_check=d.get("san_check"),
            combat=d.get("combat"),
            npc_intro=d.get("npc_intro"),
            clue_reward=d.get("clue_reward"),
        )


@dataclass
class EncounterTable:
    """一张遭遇表——从列表中按权重随机抽取。"""

    table_id: str
    encounters: list[RandomEncounter] = field(default_factory=list)

    def roll(self, rng: random.Random | None = None) -> RandomEncounter | None:
        if not self.encounters:
            return None
        rng = rng or random.Random()
        total = sum(e.weight for e in self.encounters)
        if total <= 0:
            return None
        roll = rng.randint(1, total)
        running = 0
        for e in self.encounters:
            running += e.weight
            if roll <= running:
                return e
        return self.encounters[-1]

    @classmethod
    def from_dict(cls, d: dict) -> "EncounterTable":
        encounters = [RandomEncounter.from_dict(e) for e in d.get("encounters", []) or []]
        return cls(table_id=str(d.get("table_id", "") or ""), encounters=encounters)


# ── 线索变体 ────────────────────────────────────────


@dataclass
class ClueVariant:
    """一条线索的多个可能位置。

    同一个关键线索（id）可以出现在不同场景，每次开局随机选一个。
    """

    clue_id: str
    text: str                              # 线索描述
    possible_scenes: list[str] = field(default_factory=list)  # 可能出现的场景 ID 列表
    current_scene: str = ""                # 本次开局选中的场景

    @classmethod
    def from_dict(cls, d: dict) -> "ClueVariant":
        return cls(
            clue_id=str(d.get("clue_id", "") or ""),
            text=str(d.get("text", "") or ""),
            possible_scenes=[str(s) for s in d.get("possible_scenes", []) or []],
        )


# ── NPC 变体 ────────────────────────────────────────


@dataclass
class NpcVariant:
    """一个 NPC 的多种版本。每次开局随机选一种性格/态度/秘密。"""

    npc_name: str
    variants: list[dict] = field(default_factory=list)
    # 每个 variant: {"attitude": "wary", "secret": "他知道但不敢说", "description": "..."}
    chosen: int = 0                        # 本次选中的 variant 索引

    @classmethod
    def from_dict(cls, d: dict) -> "NpcVariant":
        return cls(
            npc_name=str(d.get("npc_name", "") or ""),
            variants=d.get("variants", []) or [],
        )

    def pick(self, rng: random.Random | None = None) -> dict:
        rng = rng or random.Random()
        if not self.variants:
            return {}
        self.chosen = rng.randint(0, len(self.variants) - 1)
        return self.variants[self.chosen]


# ── 氛围变体 ────────────────────────────────────────


@dataclass
class MoodVariant:
    """一个场景的多种氛围描述。不同开局选不同的文字细节。"""

    scene_id: str
    base_description: str = ""             # 固定描述（剧情核心）
    variable_details: list[str] = field(default_factory=list)  # 可变细节池
    chosen_details: list[str] = field(default_factory=list)    # 本次选中的

    @classmethod
    def from_dict(cls, d: dict) -> "MoodVariant":
        return cls(
            scene_id=str(d.get("scene_id", "") or ""),
            base_description=str(d.get("base_description", "") or ""),
            variable_details=[str(v) for v in d.get("variable_details", []) or []],
        )

    def pick(self, count: int = 1, rng: random.Random | None = None) -> str:
        """返回组装好的完整描述（固定 + 随机选取的细节）。"""
        rng = rng or random.Random()
        if not self.variable_details:
            return self.base_description
        self.chosen_details = rng.sample(
            self.variable_details,
            min(count, len(self.variable_details)),
        )
        details_text = " ".join(self.chosen_details)
        return f"{self.base_description} {details_text}" if details_text else self.base_description


# ── 开局管理器 ──────────────────────────────────────


class RunSeed:
    """管理一次开局的所有随机选择。可序列化，支持"同一 seed 重现"。"""

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed or random.randint(0, 2**31 - 1)
        self.rng = random.Random(self.seed)
        self.clue_placements: dict[str, str] = {}     # {clue_id: scene_id}
        self.npc_choices: dict[str, int] = {}          # {npc_name: variant_index}
        self.mood_choices: dict[str, list[str]] = {}   # {scene_id: [chosen_details]}
        self.active_encounters: list[str] = []         # 本次已触发的遭遇 ID

    def place_clues(self, variants: list[ClueVariant]) -> None:
        """随机为每条可变线索分配场景。"""
        for cv in variants:
            if cv.possible_scenes:
                cv.current_scene = self.rng.choice(cv.possible_scenes)
                self.clue_placements[cv.clue_id] = cv.current_scene

    def pick_npcs(self, variants: list[NpcVariant]) -> None:
        """为每个 NPC 随机选择性格版本。"""
        for nv in variants:
            nv.pick(self.rng)
            self.npc_choices[nv.npc_name] = nv.chosen

    def pick_moods(self, variants: list[MoodVariant] | dict[str, MoodVariant], count_per_scene: int = 2) -> None:
        """为每个场景随机选择氛围细节。接受 list 或 {scene_id: MoodVariant} dict。"""
        items = variants.values() if isinstance(variants, dict) else variants
        for mv in items:
            mv.pick(count=count_per_scene, rng=self.rng)
            # 重建完整描述
            self.mood_choices[mv.scene_id] = mv.chosen_details

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "clue_placements": self.clue_placements,
            "npc_choices": self.npc_choices,
            "mood_choices": self.mood_choices,
            "active_encounters": self.active_encounters,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunSeed":
        rs = cls(seed=d.get("seed", 0))
        rs.clue_placements = d.get("clue_placements", {})
        rs.npc_choices = d.get("npc_choices", {})
        rs.mood_choices = d.get("mood_choices", {})
        rs.active_encounters = d.get("active_encounters", [])
        return rs


# ── 模组变体总配置 ──────────────────────────────────


@dataclass
class ModuleVariance:
    """一个模组的 roguelike 变体配置。

    从 scenario.json 的 "variance" 段加载。
    """

    encounter_tables: dict[str, EncounterTable] = field(default_factory=dict)
    clue_variants: list[ClueVariant] = field(default_factory=list)
    npc_variants: list[NpcVariant] = field(default_factory=list)
    mood_variants: dict[str, MoodVariant] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ModuleVariance":
        if not d:
            return cls()

        tables = {}
        for t in d.get("encounter_tables", []) or []:
            et = EncounterTable.from_dict(t)
            tables[et.table_id] = et

        clues = [ClueVariant.from_dict(c) for c in d.get("clue_variants", []) or []]
        npcs = [NpcVariant.from_dict(n) for n in d.get("npc_variants", []) or []]

        moods = {}
        for m in d.get("mood_variants", []) or []:
            mv = MoodVariant.from_dict(m)
            moods[mv.scene_id] = mv

        return cls(
            encounter_tables=tables,
            clue_variants=clues,
            npc_variants=npcs,
            mood_variants=moods,
        )
