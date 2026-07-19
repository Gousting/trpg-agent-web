"""Build a short, player-facing rules summary (Chinese) from the active system profile.

System-agnostic: it reads the declarative :class:`~dmbot.rules.profile.SystemProfile` fields
(dice, resolution, the difficulty ladder, degrees rule, auto-bands, crit, damage) and renders the
*essentials* a player needs at the table — so the `!rules` command stays in sync with whatever
profile is loaded, never a hand-maintained copy. Pure function → unit-testable without Discord.
"""

from __future__ import annotations

import json

from .profile import SystemProfile


def _ladder_lines(profile: SystemProfile) -> list[str]:
    # 从最简单开始（最高修正值在上），匹配难度表的通常阅读方式。
    items = sorted(profile.difficulty_ladder.items(), key=lambda kv: kv[1], reverse=True)
    return [f"• **{name}**: {mod:+d}" for name, mod in items]


def rules_pages_zh(profile: SystemProfile) -> list[tuple[str, str]]:
    """Return the rules essentials as ``(page_title, page_body)`` pairs, Chinese, in reading order.
    Only includes a page when the profile actually declares the relevant fields."""
    pages: list[tuple[str, str]] = []
    dice = profile.dice

    # 1) How a test works — phrased per the resolution kind.
    if profile.resolution == "roll_under":
        how = (
            f"掷 **{dice}**——你需要掷出**等于或低于**你的目标值。\n\n"
            "**目标值** = 你的技能/属性值 + 难度修正。\n\n"
            "**主持人替你掷骰**：掷骰按钮出现后，结果和成功等级会立即显示。"
        )
    elif profile.resolution == "roll_over":
        how = (
            f"掷 **{dice}**——你需要掷出**等于或高于**你的目标值。\n\n"
            "**目标值** = 你的数值 + 难度修正。主持人替你掷骰。"
        )
    else:
        how = f"掷 **{dice}**（结算方式：{profile.resolution}）。主持人替你掷骰。"
    pages.append(("检定如何进行", how))

    # 2) Difficulty ladder.
    if profile.difficulty_ladder:
        body = "主持人选择难度；修正值加到目标值上：\n\n"
        body += "\n".join(_ladder_lines(profile))
        if profile.default_difficulty:
            body += f"\n\n未指定时的默认难度：**{profile.default_difficulty}**。"
        pages.append(("难度等级", body))

    # 3) Success degrees, auto-bands, crit/fumble.
    lines: list[str] = []
    if profile.degrees == "tens_difference":
        lines.append(
            "**成功等级（EG）** = 目标值的十位数 − 掷骰结果的十位数（绝对值）。"
            "更多EG = 更明确的结果。"
        )
    if profile.auto_success_max:
        lines.append(f"**01–{profile.auto_success_max:02d}**：必定成功（险胜）。")
    if profile.auto_fail_min:
        lines.append(f"**{profile.auto_fail_min}–00**：必定失败（险败）。")
    if profile.crit == "doubles":
        lines.append(
            "**对子**（11, 22, … 99, 00）：战斗中为暴击（成功时）或大失败（失败时）。"
        )
    if lines:
        pages.append(("成功、成功等级与特殊情况", "\n\n".join(lines)))

    # 4) Damage.
    dmg = profile.damage if isinstance(profile.damage, str) else json.dumps(profile.damage, ensure_ascii=False)
    if dmg:
        pages.append(("伤害", f"**伤害：**{dmg}"))

    return pages
