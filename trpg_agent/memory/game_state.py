"""COC 游戏状态 — "硬事实"层，代码拥有，LLM 绝不直接写入。

设计原则（来自 DMbot golden rule #3）：
- LLM 只提议叙事层内容（recap 摘要、NPC 记忆文本）
- 所有硬状态（HP、SAN、NPC 态度、位置）由代码确定性操作
- 状态持久化为 JSON，原子写入，崩溃安全

Phase 2 最小集：调查员状态 + NPC + 场景 + 任务日志。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────

# NPC 态度五级量表（存储用英文 key，展示用中文 label）
ATTITUDE_SCALE = ("hostile", "wary", "neutral", "friendly", "loyal")
ATTITUDE_LABELS: dict[str, str] = {
    "hostile": "敌对",
    "wary": "警惕",
    "neutral": "中立",
    "friendly": "友好",
    "loyal": "忠诚",
}

# ── 数据类 ────────────────────────────────────────────


@dataclass
class Investigator:
    """调查员——代码拥有的可变状态层（HP/SAN/Luck 会变）。"""

    name: str
    hp: int
    max_hp: int
    san: int
    max_san: int
    luck: int
    skills: dict[str, int] = field(default_factory=dict)   # {技能名: 值}
    conditions: list[str] = field(default_factory=list)     # ["重伤", "临时疯狂: 恐惧症"]
    inventory: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name, "hp": self.hp, "max_hp": self.max_hp,
            "san": self.san, "max_san": self.max_san, "luck": self.luck,
        }
        if self.skills:
            d["skills"] = self.skills
        if self.conditions:
            d["conditions"] = list(self.conditions)
        if self.inventory:
            d["inventory"] = list(self.inventory)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Investigator":
        return cls(
            name=str(d["name"]),
            hp=int(d.get("hp", d.get("max_hp", 0))),
            max_hp=int(d.get("max_hp", 0)),
            san=int(d.get("san", d.get("max_san", 0))),
            max_san=int(d.get("max_san", 0)),
            luck=int(d.get("luck", 0)),
            skills={str(k): int(v) for k, v in d.get("skills", {}).items()},
            conditions=list(d.get("conditions", []) or []),
            inventory=list(d.get("inventory", []) or []),
        )

    def take_damage(self, amount: int) -> int:
        """扣 HP，返回实际伤害值。不低于 0。"""
        actual = min(self.hp, amount)
        self.hp = max(0, self.hp - amount)
        if self.hp == 0 and "重伤" not in self.conditions:
            self.conditions.append("重伤")
        return actual

    def lose_san(self, amount: int) -> int:
        """扣 SAN，返回实际损失。不低于 0。"""
        actual = min(self.san, amount)
        self.san = max(0, self.san - amount)
        return actual

    def heal(self, amount: int) -> int:
        """回复 HP。"""
        old = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        if self.hp > 0 and "重伤" in self.conditions:
            self.conditions.remove("重伤")
        return self.hp - old

    @property
    def is_downed(self) -> bool:
        return self.hp <= 0

    @property
    def is_insane(self) -> bool:
        return self.san <= 0


@dataclass
class Npc:
    """NPC——简化版。无 WH40k 的 psyker/warp/agenda 系统。"""

    name: str
    attitude: str = "neutral"       # hostile/wary/neutral/friendly/loyal
    description: str = ""           # 一句话描述，注入 prompt
    location: str = ""              # 当前所在位置

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.attitude != "neutral":
            d["attitude"] = self.attitude
        if self.description:
            d["description"] = self.description
        if self.location:
            d["location"] = self.location
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Npc":
        return cls(
            name=str(d["name"]),
            attitude=str(d.get("attitude", "neutral") or "neutral"),
            description=str(d.get("description", "") or ""),
            location=str(d.get("location", "") or ""),
        )


@dataclass
class Quest:
    """任务/线索追踪。"""

    title: str
    status: str = "open"   # open | resolved | failed

    def to_dict(self) -> dict:
        return {"title": self.title, "status": self.status}

    @classmethod
    def from_dict(cls, d: dict) -> "Quest":
        return cls(title=str(d["title"]), status=str(d.get("status", "open") or "open"))


@dataclass
class GameState:
    """COC 游戏世界状态——一局 session 的可变数据。"""

    system: str = "coc_7e"
    session_id: str = ""
    location: str = ""                          # 当前场景
    investigators: list[Investigator] = field(default_factory=list)
    npcs: list[Npc] = field(default_factory=list)
    quests: list[Quest] = field(default_factory=list)
    recap: str = ""                             # 前情提要（LLM 生成，代码存储）
    turn_count: int = 0

    # ── 序列化 ──────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "system": self.system,
            "session_id": self.session_id,
            "location": self.location,
            "investigators": [c.to_dict() for c in self.investigators],
            "npcs": [n.to_dict() for n in self.npcs],
            "quests": [q.to_dict() for q in self.quests],
            "recap": self.recap,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        return cls(
            system=str(d.get("system", "coc_7e") or "coc_7e"),
            session_id=str(d.get("session_id", "") or ""),
            location=str(d.get("location", "") or ""),
            investigators=[Investigator.from_dict(c) for c in d.get("investigators", []) or []],
            npcs=[Npc.from_dict(n) for n in d.get("npcs", []) or []],
            quests=[Quest.from_dict(q) for q in d.get("quests", []) or []],
            recap=str(d.get("recap", "") or ""),
            turn_count=int(d.get("turn_count", 0) or 0),
        )

    @classmethod
    def load(cls, path: Path) -> "GameState | None":
        """从文件加载。不存在返回 None。"""
        if not path.is_file():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        """原子写入——崩溃安全。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ── 查找 ──────────────────────────────────────

    def find_investigator(self, name: str) -> Investigator | None:
        key = name.strip()
        for inv in self.investigators:
            if inv.name == key:
                return inv
        return None

    def find_npc(self, name: str) -> Npc | None:
        key = name.strip()
        for npc in self.npcs:
            if npc.name == key:
                return npc
        return None

    # ── 场景状态摘要（注入 prompt） ─────────────────

    def scene_summary(self) -> str:
        """生成当前场景的状态摘要文本。"""
        lines = []

        if self.location:
            lines.append(f"当前场景：{self.location}")

        if self.investigators:
            lines.append("\n调查员：")
            for inv in self.investigators:
                status = f"HP {inv.hp}/{inv.max_hp}, SAN {inv.san}/{inv.max_san}"
                if inv.conditions:
                    status += f", {' '.join(inv.conditions)}"
                lines.append(f"  {inv.name} — {status}")

        if self.npcs:
            present = [n for n in self.npcs if n.location == self.location]
            if present:
                lines.append("\n在场 NPC：")
                for npc in present:
                    attitude_label = ATTITUDE_LABELS.get(npc.attitude, npc.attitude)
                    desc = f"（{attitude_label}）{npc.description}" if npc.description else f"（{attitude_label}）"
                    lines.append(f"  {npc.name} {desc}")

        if self.quests:
            open_quests = [q for q in self.quests if q.status == "open"]
            if open_quests:
                lines.append("\n当前线索：")
                for q in open_quests:
                    lines.append(f"  - {q.title}")

        return "\n".join(lines)

    # ── NPC 态度 ──────────────────────────────────

    def step_attitude(self, name: str, proposed: str) -> str | None:
        """尝试推进 NPC 态度，每次最多 ±1 步。返回新态度或 None。"""
        npc = self.find_npc(name)
        if not npc:
            return None
        key = proposed.strip().lower()
        if key not in ATTITUDE_SCALE:
            return None
        current = npc.attitude.strip().lower()
        cur_idx = ATTITUDE_SCALE.index(current) if current in ATTITUDE_SCALE else ATTITUDE_SCALE.index("neutral")
        target_idx = ATTITUDE_SCALE.index(key)
        new_idx = cur_idx + max(-1, min(1, target_idx - cur_idx))
        npc.attitude = ATTITUDE_SCALE[new_idx]
        return npc.attitude
