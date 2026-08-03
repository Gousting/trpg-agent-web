"""无限流强化系统 —— 天赋加载、购买校验、效果应用。

纯数据驱动：强化定义在 data/infinite_flow/talents.json，本模块提供
加载 / 查询 / 购买校验 / 效果应用。购买会修改 Reincarnator 的属性与
talents 列表——属性是硬状态，由代码确定性操作，LLM 不直接写入。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from trpg_agent.memory.game_state import Reincarnator

log = logging.getLogger(__name__)

TALENTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "infinite_flow" / "talents.json"


@dataclass
class Talent:
    """一个强化项。"""

    id: str
    name: str
    line: str          # 力量 / 敏捷 / 精神
    level: int
    description: str = ""
    requires: list[str] = field(default_factory=list)   # 前置强化 ID
    effects: dict[str, int] = field(default_factory=dict)  # {属性键: 数值}

    @classmethod
    def from_dict(cls, d: dict) -> "Talent":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            line=str(d.get("line", "")),
            level=int(d.get("level", 1) or 1),
            description=str(d.get("description", "") or ""),
            requires=list(d.get("requires", []) or []),
            effects={str(k): int(v) for k, v in (d.get("effects", {}) or {}).items()},
        )


@dataclass
class TalentCatalog:
    """强化树目录——从 talents.json 加载，提供查询与购买逻辑。"""

    talents: dict[str, Talent] = field(default_factory=dict)
    cost_per_level: int = 1

    @classmethod
    def load(cls, path: Path | None = None) -> "TalentCatalog":
        p = path or TALENTS_PATH
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("强化树文件不存在: %s，返回空目录", p)
            return cls()
        except json.JSONDecodeError:
            log.error("强化树文件解析失败: %s", p)
            return cls()
        talents = {t.id: t for t in (Talent.from_dict(d) for d in data.get("talents", []) or [])}
        return cls(
            talents=talents,
            cost_per_level=int(data.get("cost_per_level", 1) or 1),
        )

    def line_talents(self, line: str) -> list[Talent]:
        """某属性线的全部强化（按等级升序）。"""
        return sorted(
            (t for t in self.talents.values() if t.line == line),
            key=lambda t: t.level,
        )

    def available_for(self, rein: Reincarnator) -> list[Talent]:
        """当前可购买的强化（前置满足 + 未购买 + AP 足够）。"""
        result = []
        for t in self.talents.values():
            if t.id in rein.talents:
                continue
            if not all(req in rein.talents for req in t.requires):
                continue
            if rein.ap < self.cost_per_level:
                continue
            result.append(t)
        return sorted(result, key=lambda t: (t.line, t.level))

    def purchase(self, rein: Reincarnator, talent_id: str) -> tuple[bool, str]:
        """购买强化。成功返回 (True, 描述)；失败返回 (False, 原因)。"""
        t = self.talents.get(talent_id)
        if t is None:
            return False, f"强化 {talent_id} 不存在"
        if t.id in rein.talents:
            return False, "已购买该强化"
        if not all(req in rein.talents for req in t.requires):
            return False, f"前置强化未解锁：{', '.join(t.requires)}"
        if rein.ap < self.cost_per_level:
            return False, f"强化点不足（需要 {self.cost_per_level}，当前 {rein.ap}）"

        # 应用效果
        rein.ap -= self.cost_per_level
        for key, val in t.effects.items():
            if key == "strength":
                rein.strength += val
            elif key == "agility":
                rein.agility += val
            elif key == "spirit":
                rein.spirit += val
            elif key == "max_hp":
                rein.max_hp += val
                rein.hp = min(rein.max_hp, rein.hp + val)
            elif key == "melee_bonus":
                rein.bonus_melee += val
            elif key == "dodge_bonus":
                rein.bonus_dodge += val
            elif key == "spirit_resist_bonus":
                rein.bonus_resist += val
            # 其他键忽略（保留扩展空间）
        rein.talents.append(t.id)
        return True, f"已习得「{t.name}」"
