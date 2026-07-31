"""战斗遭遇数据模型 —— 敌人、环境、胜负条件与叙事后果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Enemy:
    """单个敌人或敌人组。"""

    id: str
    name: str
    hp: int
    hp_max: int = 0
    armor: int = 0
    attack_bonus: int = 0
    damage: str = "1d4"
    abilities: list[dict[str, str]] = field(default_factory=list)
    behavior: str = ""
    count: int = 1  # 同类型敌人数量

    def __post_init__(self) -> None:
        if self.hp_max == 0:
            self.hp_max = self.hp

    @classmethod
    def from_dict(cls, d: dict) -> "Enemy":
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            hp=int(d.get("hp", 0)),
            armor=int(d.get("armor", 0)),
            attack_bonus=int(d.get("attack_bonus", 0)),
            damage=str(d.get("damage", "1d4")),
            abilities=[dict(a) for a in d.get("abilities", []) or []],
            behavior=str(d.get("behavior", "")),
            count=int(d.get("count", 1)),
        )

    def is_alive(self) -> bool:
        return self.hp > 0

    def take_damage(self, amount: int) -> int:
        """受到伤害，返回实际扣血量。"""
        effective = max(0, amount - self.armor)
        self.hp = max(0, self.hp - effective)
        return effective


@dataclass
class CombatEnvironment:
    """战斗场景的环境参数。"""

    terrain: str = ""
    hazards: list[str] = field(default_factory=list)
    lighting: str = "normal"  # normal / dim / dark

    @classmethod
    def from_dict(cls, d: dict) -> "CombatEnvironment":
        if d is None:
            return cls()
        return cls(
            terrain=str(d.get("terrain", "")),
            hazards=[str(h) for h in d.get("hazards", []) or []],
            lighting=str(d.get("lighting", "normal")),
        )


@dataclass
class CombatOutcome:
    """一个可能的战斗结局。"""

    id: str  # victory / defeat / flee
    label: str = ""
    condition: str = ""
    provides_clues: list[str] = field(default_factory=list)
    next_location_type: str = ""
    consequence: str = ""
    reward: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "CombatOutcome":
        return cls(
            id=str(d.get("id", "")),
            label=str(d.get("label", "")),
            condition=str(d.get("condition", "")),
            provides_clues=[str(c) for c in d.get("provides_clues", []) or []],
            next_location_type=str(d.get("next_location_type", "")),
            consequence=str(d.get("consequence", "")),
            reward=str(d.get("reward", "")),
        )


@dataclass
class CombatEncounter:
    """一次完整的战斗遭遇 —— 由战斗模块编译生成。"""

    id: str
    title: str
    difficulty: int = 2
    description: str = ""
    enemies: list[Enemy] = field(default_factory=list)
    environment: CombatEnvironment = field(default_factory=CombatEnvironment)
    special_rules: list[str] = field(default_factory=list)
    escalation: list[str] = field(default_factory=list)  # 逐轮升级叙事（[第2轮, 第3轮, ...]）
    outcomes: dict[str, CombatOutcome] = field(default_factory=dict)
    scaling: dict[str, Any] = field(default_factory=dict)
    image: str = ""
    image_prompt: str = ""

    # 运行时状态
    round_number: int = 0
    active: bool = False
    resolved_outcome: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "CombatEncounter":
        outcomes_raw = d.get("outcomes", {}) or {}
        outcomes = {}
        for key, val in outcomes_raw.items():
            if isinstance(val, dict):
                oc = CombatOutcome.from_dict(val)
                oc.id = key
                outcomes[key] = oc

        return cls(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")),
            difficulty=int(d.get("difficulty", 2)),
            description=str(d.get("description", "")),
            enemies=[Enemy.from_dict(e) for e in d.get("enemies", []) or []],
            environment=CombatEnvironment.from_dict(d.get("environment")),
            special_rules=[str(r) for r in d.get("rules", []) or d.get("special_rules", []) or []],
            escalation=[str(e) for e in d.get("escalation", []) or []],
            outcomes=outcomes,
            scaling=dict(d.get("scaling", {}) or {}),
            image=str(d.get("image", "")),
            image_prompt=str(d.get("image_prompt", "")),
        )

    def all_enemies_dead(self) -> bool:
        return all(not e.is_alive() for e in self.enemies)

    def living_enemies(self) -> list[Enemy]:
        return [e for e in self.enemies if e.is_alive()]

    @staticmethod
    def combat_scene_id(module_id: str) -> str:
        """战斗遭遇场景的统一 ID 格式。"""
        return f"{module_id}::combat_encounter"

    @staticmethod
    def outcome_scene_id(module_id: str, outcome_id: str) -> str:
        """战斗结局过渡场景的统一 ID 格式。"""
        return f"{module_id}::combat_{outcome_id}"

    def apply_scaling(self, player_count: int, *, party_hp_ratio: float = 1.0) -> None:
        """根据玩家数量 + 队伍状态缩放敌人。

        参数:
            player_count: 调查员人数
            party_hp_ratio: 队伍平均 HP 比例（0.0-1.0），满血 1.0，半血 0.5。
                低于 0.4 时敌人 HP 减半（残血队伍遇到满强度敌人不合理）。
        """
        sc = self.scaling

        # 队伍残血 → 削弱敌人 HP
        if party_hp_ratio < 0.4 and self.enemies:
            for enemy in self.enemies:
                enemy.hp = max(1, int(enemy.hp * 0.5))
                enemy.hp_max = max(1, int(enemy.hp_max * 0.5))

        if not sc or player_count <= 3:
            return

        extra = player_count - 3
        per_extra = sc.get("per_extra_player", {}) or {}
        if "enemies" in per_extra and self.enemies:
            target = self.enemies[-1]
            target.count += extra
        hp_bonus = per_extra.get("head_orderly_hp") or per_extra.get("boss_hp")
        if hp_bonus and self.enemies:
            self.enemies[0].hp += hp_bonus * extra
            self.enemies[0].hp_max += hp_bonus * extra

    def start(self) -> None:
        """开始战斗。"""
        self.round_number = 0
        self.active = True
        self.resolved_outcome = ""

    def check_outcome(self, investigators_fled: bool = False, investigators_down: bool = False) -> str:
        """检查是否达成任何结局条件，返回结局 ID 或空字符串。"""
        if investigators_fled and "flee" in self.outcomes:
            return "flee"
        if investigators_down and "defeat" in self.outcomes:
            return "defeat"
        if self.all_enemies_dead() and "victory" in self.outcomes:
            return "victory"
        return ""
