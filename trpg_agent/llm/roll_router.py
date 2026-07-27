"""检定路由 — 独立的分类器，判断玩家行动是否需要技能检定。

设计原则（沿用旧版 ADR 014 的约束式路由思路）：
- 叙事模型天然倾向于跳过检定（"好的，你成功了"）
- 同一个模型做分类比做叙述更可靠——独立的 constrained-JSON 调用
- 分类器只判断"要不要检定 + 什么技能 + 什么难度"，不动骰子

纯 prompt/schema/parse 函数，无需 LLM 即可单元测试。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class RollRequest:
    """一次检定请求——分类器的输出，引擎的输入。"""

    skill: str              # 技能名（如"侦查"）
    difficulty: str = "常规"  # 常规/困难/极难
    character: str | None = None  # 哪位调查员（可选）
    source: str = "router"   # "router" | "marker"

    def to_context(self, roll_result: str) -> str:
        """生成注入 prompt 的检定上下文。"""
        who = f"（{self.character}）" if self.character else ""
        return f"{self.skill}{who} {self.difficulty}难度: {roll_result}"


def classifier_schema(skills: list[str], difficulties: list[str]) -> dict:
    """JSON Schema，限制模型只能从给定集合中选择技能和难度。"""
    return {
        "type": "object",
        "properties": {
            "needs_test": {"type": "boolean"},
            "skill": {"type": "string", "enum": [*skills, ""]},
            "difficulty": {"type": "string", "enum": [*difficulties, ""]},
            "character": {"type": "string"},
        },
        "required": ["needs_test", "skill", "difficulty"],
    }


def classifier_prompt(skills: list[str], difficulties: list[str]) -> str:
    """分类器的系统 prompt——中文，无叙述，无历史。"""
    skill_list = "、".join(skills) if skills else "无"
    diff_list = "、".join(difficulties) if difficulties else "常规"
    return (
        "你是《克苏鲁的呼唤》的规则助手。\n"
        "判断下方的玩家行动是否需要技能检定（掷骰）。\n\n"
        "需要检定的情况：结果不确定的行动，如潜行、侦查、说服、攀爬、"
        "战斗、识破谎言、撬锁、回忆知识……\n"
        "不需要检定的情况：确定能成功的普通行动（走路、打开未锁的门、正常交谈）、"
        "纯扮演对话、或对世界提出的问题。\n\n"
        f"技能只能从以下列表中选：{skill_list}。\n"
        f"难度只能从以下列表中选：{diff_list}。默认选「常规」。\n"
        "character 填写执行该行动的调查员名字（从玩家输入中判断），不清楚则留空。\n\n"
        "如果不需要检定：needs_test=false，skill、difficulty 和 character 全部留空。"
        "只输出 JSON，不要其他内容。"
    )


def parse_router_response(data: dict) -> RollRequest | None:
    """将分类器的 JSON 输出解析为 RollRequest。无检定返回 None。"""
    if not isinstance(data, dict):
        return None
    if not data.get("needs_test"):
        return None
    skill = str(data.get("skill") or "").strip()
    if not skill:
        return None
    difficulty = str(data.get("difficulty") or "").strip() or "常规"
    character = str(data.get("character") or "").strip() or None
    return RollRequest(
        skill=skill, difficulty=difficulty,
        character=character, source="router",
    )


# ── 标记解析（备用方案）──────────────────────────────

_MARKER_RE = re.compile(
    r"\<\<检定\s+"
    r"(?P<skill>\S+?)\s*"
    r"(?P<difficulty>困难|极难|常规)?\s*"
    r"(?:对\s*(?P<character>\S+?))?\s*"
    r"\>\>"
)


def parse_markers(text: str) -> list[RollRequest]:
    """从文本中解析 <<检定 技能 难度 对 角色>> 标记。

    示例：<<检定 侦查 困难 对 陈明>>
    这是备用方案——当分类器关闭时使用，或当 KP 叙述模型
    自行输出了标记时兜底提取。
    """
    results = []
    for m in _MARKER_RE.finditer(text):
        skill = m.group("skill")
        if not skill:
            continue
        difficulty = m.group("difficulty") or "常规"
        character = m.group("character") or None
        results.append(RollRequest(
            skill=skill, difficulty=difficulty,
            character=character, source="marker",
        ))
    return results


def clean_markers(text: str) -> str:
    """从文本中移除所有 <<检定 ...>> 标记。"""
    return _MARKER_RE.sub("", text).strip()
