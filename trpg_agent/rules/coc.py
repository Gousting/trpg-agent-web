"""COC 7 版检定引擎 — d100 掷骰 + 成功等级判定。

纯函数，无状态，无副作用。接受 skill_value 和 difficulty，返回结构化结果。
支持常规/困难/极难三级难度、大成功(01)、大失败判定。

所有函数接受显式 `rng: random.Random` 参数，测试可 seed。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .engine import DiceRoll, roll


class SuccessLevel(Enum):
    """COC 7 版成功等级"""

    CRITICAL = "大成功"          # 骰值 = 1
    EXTREME = "极限成功"          # 骰值 ≤ 技能值的 1/5
    HARD = "困难成功"             # 骰值 ≤ 技能值的 1/2
    REGULAR = "常规成功"          # 骰值 ≤ 技能值
    FAILURE = "失败"              # 骰值 > 技能值
    FUMBLE = "大失败"            # 96-100（技能<50）或 100（技能≥50）


@dataclass(frozen=True, slots=True)
class CocTestResult:
    """COC 检定结果"""

    roll: int              # d100 实际值 (1-100)
    skill_value: int        # 技能值
    difficulty: str         # "常规" | "困难" | "极难"
    target: int             # 经过难度修正的有效目标值
    success: bool
    level: SuccessLevel
    is_critical: bool
    is_fumble: bool


def _effective_target(skill_value: int, difficulty: str) -> int:
    """根据难度计算有效目标值。

    常规：技能值原值
    困难：技能值 × 1/2（向下取整）
    极难：技能值 × 1/5（向下取整）
    """
    if difficulty in ("极难", "extreme", "极限"):
        return max(1, skill_value // 5)
    if difficulty in ("困难", "hard"):
        return max(1, skill_value // 2)
    return max(1, skill_value)  # 常规


def _is_fumble(face: int, skill_value: int) -> bool:
    """判断大失败。

    技能值 < 50：96-100 为大失败
    技能值 ≥ 50：仅 100 为大失败
    """
    if skill_value < 50:
        return face >= 96
    return face == 100


def resolve_coc(
    skill_value: int,
    difficulty: str = "常规",
    *,
    rng: random.Random | None = None,
) -> CocTestResult:
    """执行一次 COC 检定，返回完整结果。

    Args:
        skill_value: 技能值（1-99，越高越好）
        difficulty: "常规" | "困难" | "极难"
        rng: 随机数生成器

    Returns:
        CocTestResult 包含骰值、目标值、成功等级等完整信息
    """
    rng = rng or random.Random()
    dice = roll("1d100", rng)
    face = dice.total  # 1-100 (注：0 = 100)
    if face == 0:
        face = 100

    effective = _effective_target(skill_value, difficulty)

    if face == 1:
        level = SuccessLevel.CRITICAL
    elif _is_fumble(face, skill_value):
        level = SuccessLevel.FUMBLE
    elif face <= skill_value // 5:
        level = SuccessLevel.EXTREME
    elif face <= skill_value // 2:
        level = SuccessLevel.HARD
    elif face <= effective:
        level = SuccessLevel.REGULAR
    else:
        level = SuccessLevel.FAILURE

    return CocTestResult(
        roll=face,
        skill_value=skill_value,
        difficulty=difficulty,
        target=effective,
        success=level != SuccessLevel.FAILURE and level != SuccessLevel.FUMBLE,
        level=level,
        is_critical=level == SuccessLevel.CRITICAL,
        is_fumble=level == SuccessLevel.FUMBLE,
    )


def describe_result(result: CocTestResult, skill_name: str = "") -> str:
    """将检定结果转换为中文描述文本，供 KP 叙述使用。"""
    prefix = f"{skill_name} " if skill_name else ""
    op = "≤" if result.success else ">"
    detail = f"（骰值 {result.roll} {op} 目标 {result.target}"
    if result.difficulty != "常规":
        detail += f"，{result.difficulty}难度"
    detail += "）"

    if result.is_critical:
        return f"{prefix}大成功{detail}"
    if result.is_fumble:
        return f"{prefix}大失败{detail}"
    return f"{prefix}{result.level.value}{detail}"
