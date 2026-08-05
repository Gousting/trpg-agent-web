"""战斗机制解析器 —— 代码驱动的掷骰、伤害、结局判定。

在 CombatLoop.submit_vote() 和 LLM 叙述之间插入：
1. 从选项文本提取技能检定类型和难度
2. 掷 d100 判定成功/失败
3. 对敌人/调查员施加伤害
4. 检查胜负条件
5. 产出结构化结算结果 → LLM 拿到结果后只负责润色叙述
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from trpg_agent.combat.encounter import CombatEncounter, CombatOutcome
from trpg_agent.rules.engine import (
    TestResult,
    resolve_roll_under,
    roll_damage,
)
from trpg_agent.rules.profile import SystemProfile

# ── 内联 COC 7e 战斗 profile（避免依赖不兼容的 JSON 格式） ──
_COC_PROFILE = SystemProfile(
    name="coc_7e_combat",
    dice="1d100",
    resolution="roll_under",
    degrees="tens_difference",
    crit="doubles",
    auto_success_max=1,
    auto_fail_min=100,
)

# ── 难度映射（选项文本中的中文难度 → d100 目标值） ──
# 战斗场景不依赖调查员实际技能值，使用抽象目标值：
#   简单/普通 → 目标 60（60% 成功率）
#   困难     → 目标 40（40% 成功率）
#   极难     → 目标 25（25% 成功率）
_DIFFICULTY_TARGETS = {
    "简单": 70,
    "常规": 60,
    "普通": 60,
    "一般": 60,
    "regular": 60,
    "normal": 60,
    "困难": 40,
    "hard": 40,
    "极难": 25,
    "extreme": 25,
    "极限": 25,
}

# ── 选项文本解析正则 ──
# 匹配：STR 检定（普通）、DEX 检定（困难）、力量检定（普通） 等
_SKILL_CHECK_RE = re.compile(
    r"(STR|DEX|CON|INT|POW|CHA|SIZ|EDU"
    r"|力量|体质|敏捷|智力|意志|魅力|体型|教育"
    r"|STRENGTH|DEXTERITY|CONSTITUTION|INTELLIGENCE|POWER|CHARISMA|SIZE)"
    r"\s*检定[（(]([^)）]*?)[）)]",
    re.IGNORECASE,
)

# 匹配 SAN 损失：全体 SAN-1、SAN 检定（1d3/1d6）、SAN-1d3 等
_SAN_LOSS_RE = re.compile(
    r"(?:全体|全队|每人)?\s*SAN\s*[-−]\s*(?:检定[（(]?)?(\d+d\d+|\d+)",
    re.IGNORECASE,
)

# 匹配自动成功/失败标记
_AUTO_SUCCESS_RE = re.compile(r"自动成功", re.IGNORECASE)
_AUTO_FAIL_RE = re.compile(r"自动失败", re.IGNORECASE)


def _parse_difficulty(text: str) -> str | None:
    """从选项文本中提取难度等级。"""
    for key in _DIFFICULTY_TARGETS:
        if key in text:
            return key
    return "普通"  # 默认常规难度


def _parse_skill(text: str) -> str | None:
    """从选项文本中提取技能检定类型。"""
    m = _SKILL_CHECK_RE.search(text)
    if m:
        return m.group(1).upper()
    return None


def _parse_san_loss(text: str) -> int:
    """从选项文本中提取 SAN 损失量。"""
    m = _SAN_LOSS_RE.search(text)
    if not m:
        return 0
    dice_str = m.group(1)
    if "d" in dice_str.lower():
        # 简单解析：1d3 → 掷骰
        parts = dice_str.lower().split("d")
        count = int(parts[0]) if parts[0] else 1
        sides = int(parts[1])
        return sum(random.randint(1, sides) for _ in range(count))
    return int(dice_str)


@dataclass
class CombatMechanicResult:
    """一次战斗行动的代码结算结果。"""

    success: bool
    test_rolled: bool               # 是否真的掷了骰（自动成功/失败时不掷）
    test_result: TestResult | None   # d100 检定结果
    skill: str | None                # 被检测的技能（如 "STR"）
    difficulty: str                  # 难度描述
    target: int                      # 掷骰目标值

    # 伤害
    damage_to_enemies: dict[str, int] = field(default_factory=dict)
    damage_to_investigators: int = 0  # 调查员方承受的总伤害（叙事用）
    san_loss: int = 0

    # 结局
    outcome: CombatOutcome | None = None
    summary: str = ""                 # 结算摘要文本

    # BOSS 阶段事件（狂暴/召唤等）——[{name, behavior, ...}]
    phase_events: list[dict] = field(default_factory=list)


class CombatMechanics:
    """战斗行动代码解析器。

    用法:
        mech = CombatMechanics(encounter)
        result = mech.resolve_option(chosen_option)
        # → result.success / result.damage_to_enemies / result.outcome ...
        # → 将 result 传给 build_resolve_narration_prompt() 让 LLM 润色
    """

    def __init__(
        self,
        encounter: CombatEncounter,
        *,
        rng: random.Random | None = None,
        melee_bonus: int = 0,          # 轮回者力量 → 近战伤害加成
        dodge_bonus: int = 0,          # 轮回者敏捷 → 闪避率加成（百分比点）
        spirit_resist_bonus: int = 0,  # 轮回者精神 → 精神抗性加成（百分比点）
    ) -> None:
        self._encounter = encounter
        self._rng = rng or random.Random()
        self._profile = _COC_PROFILE
        self.melee_bonus = melee_bonus
        self.dodge_bonus = dodge_bonus
        self.spirit_resist_bonus = spirit_resist_bonus

    @property
    def encounter(self) -> CombatEncounter:
        return self._encounter

    def resolve_option(self, option_text: str) -> CombatMechanicResult:
        """解析一个被选中的战斗选项，掷骰并应用结果。

        参数:
            option_text: 被选中选项的完整描述文本

        返回:
            包含成功/失败、伤害、结局的结构化结果
        """
        skill = _parse_skill(option_text)
        difficulty = _parse_difficulty(option_text)
        target = _DIFFICULTY_TARGETS.get(difficulty, 60)
        is_auto_success = bool(_AUTO_SUCCESS_RE.search(option_text))
        is_auto_fail = bool(_AUTO_FAIL_RE.search(option_text))

        # ── 掷骰判定 ──
        test_result: TestResult | None = None
        success: bool
        test_rolled: bool

        if is_auto_success:
            success = True
            test_rolled = False
        elif is_auto_fail:
            success = False
            test_rolled = False
        else:
            test_result = resolve_roll_under(self._profile, target, self._rng)
            success = test_result.success
            test_rolled = True

        # ── 施加伤害 ──
        damage_to_enemies: dict[str, int] = {}
        damage_to_investigators = 0
        san_loss = _parse_san_loss(option_text)

        living = self._encounter.living_enemies()
        if success and living:
            # 成功：对主要敌人造成伤害（2d6 基础伤害 + 力量加成）
            dmg = sum(self._rng.randint(1, 6) for _ in range(2)) + self.melee_bonus
            primary = living[0]
            actual = primary.take_damage(dmg)
            damage_to_enemies[primary.id] = actual
        elif not success and living:
            # 失败：敌人反击（使用敌人伤害骰）；敏捷加成提供闪避概率
            dodged = self.dodge_bonus > 0 and self._rng.randint(1, 100) <= self.dodge_bonus
            if not dodged:
                for enemy in living[:2]:  # 最多两个敌人反击
                    try:
                        roll_result = roll_damage(enemy.damage, self._rng)
                        damage_to_investigators += roll_result.total
                    except Exception:
                        # 解析骰子表达式失败时用默认值
                        damage_to_investigators += self._rng.randint(1, 4)

        # 精神加成减免 SAN 损失（百分比点，最多减免一半）
        if san_loss and self.spirit_resist_bonus > 0:
            resist_chance = min(50, self.spirit_resist_bonus)
            if self._rng.randint(1, 100) <= resist_chance:
                san_loss = max(1, san_loss // 2)

        # ── 检查结局 ──
        outcome_id: str = self._encounter.check_outcome(
            investigators_down=(damage_to_investigators > 20),
        )
        outcome = self._encounter.outcomes.get(outcome_id) if outcome_id else None

        # ── BOSS 阶段触发检查（敌人 HP 跨过阈值 → 狂暴/召唤）──
        phase_events: list[dict] = []
        if outcome is None and damage_to_enemies:
            phase_events = self._encounter.check_phase_triggers()

        # ── 构建摘要 ──
        parts = []
        if skill:
            parts.append(f"{skill}检定{'成功' if success else '失败'}")
        if test_result:
            parts.append(f"（掷出{test_result.roll}，目标{target}）")
        if damage_to_enemies:
            total_dmg = sum(damage_to_enemies.values())
            parts.append(f"对敌人造成{total_dmg}点伤害")
        if damage_to_investigators:
            parts.append(f"调查员受{damage_to_investigators}点伤害")
        if san_loss:
            parts.append(f"SAN-{san_loss}")
        for ph in phase_events:
            parts.append(f"⚠ {ph.get('name', '阶段变化')}")

        return CombatMechanicResult(
            success=success,
            test_rolled=test_rolled,
            test_result=test_result,
            skill=skill,
            difficulty=difficulty or "普通",
            target=target,
            damage_to_enemies=damage_to_enemies,
            damage_to_investigators=damage_to_investigators,
            san_loss=san_loss,
            outcome=outcome,
            summary="；".join(parts) if parts else "结果混沌不清",
            phase_events=phase_events,
        )
