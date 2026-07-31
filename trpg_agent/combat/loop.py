"""战斗回合调度器 —— beat-driven 循环。

管理战斗的生命周期：开场 → 多回合循环 → 投票收集 → 行动结算 → 结局判定。

架构：
    CombatLoop 是一个有状态对象，持有一次战斗遭遇的完整运行时状态。
    外部（web_server）驱动它的推进：每收到一轮投票时调用 advance()。
    它自己决定当前处于哪个阶段、需要什么输入、何时结束。

状态机：
    ENTER   → 生成开场叙事 + 第一批三选项，等待投票
    VOTING  → 投票已收集，等待结算
    RESOLVE → 结算当前轮行动，叙述结果，检查结局
      ↑     如果未达结局，回到 ENTER（下一轮）
      ↓     如果达结局，进入 END
    END     → 输出结局叙事，标记战斗结束
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from trpg_agent.combat.encounter import CombatEncounter, CombatOutcome
from trpg_agent.combat.prompts import (
    build_combat_outcome_summary,
    build_combat_resolution_prompt,
    build_combat_system_prompt,
    build_combat_turn_user_prompt,
    build_round_escalation,
)

log = logging.getLogger(__name__)


@dataclass
class CombatOption:
    """一个赌注式战斗选项。"""
    label: str          # 选项标题（如"正面迎战长老"）
    description: str    # 完整描述（含代价、检定条件）
    option_key: str     # A / B / C


@dataclass
class CombatRoundState:
    """一个回合的完整状态。"""
    round_number: int
    opening_narration: str = ""             # 开场叙事
    options: list[CombatOption] = field(default_factory=list)
    chosen_option_key: str = ""             # 被投票选中的选项
    chosen_option: CombatOption | None = None
    resolution_narration: str = ""          # 结算叙事
    summary: str = ""                       # 本回合摘要（记录到历史）


@dataclass
class CombatState:
    """战斗全局运行时状态。"""
    encounter: CombatEncounter
    # 回合历史
    rounds: list[CombatRoundState] = field(default_factory=list)
    # 当前轮状态
    current_round: int = 0
    phase: str = "enter"        # enter | voting | resolve | end
    # 调查员状态（外部传入的文本描述）
    investigators_state: str = ""
    # 结局
    outcome: CombatOutcome | None = None
    outcome_id: str = ""
    # 错误状态
    error: str = ""


class CombatLoop:
    """一次战斗遭遇的完整生命周期管理器。

    用法:
        loop = CombatLoop(encounter, investigators_state="...")
        # 第一轮：生成开场叙事 + 选项
        round_state = loop.start_round()
        # → 展示叙事 + 选项给观众，等待投票结果
        # → 收到投票后：
        loop.submit_vote("A")
        outcome = loop.resolve()
        # → 如果 outcome 为空，战斗继续 → 调用 start_round() 开始下一轮
        # → 如果 outcome 非空，战斗结束
    """

    def __init__(
        self,
        encounter: CombatEncounter,
        *,
        investigators_state: str = "",
    ) -> None:
        self._state = CombatState(
            encounter=encounter,
            investigators_state=investigators_state,
        )
        encounter.start()

    # ── 属性访问 ──

    @property
    def is_active(self) -> bool:
        return self._state.encounter.active and self._state.phase != "end"

    @property
    def is_ended(self) -> bool:
        return self._state.phase == "end"

    @property
    def outcome(self) -> CombatOutcome | None:
        return self._state.outcome

    @property
    def current_round(self) -> int:
        return self._state.current_round

    @property
    def current_phase(self) -> str:
        return self._state.phase

    @property
    def current_options(self) -> list[CombatOption]:
        if self._state.rounds:
            return self._state.rounds[-1].options
        return []

    @property
    def last_narration(self) -> str:
        """最近一次的叙事文本（开场或结算）。"""
        if not self._state.rounds:
            return ""
        r = self._state.rounds[-1]
        return r.resolution_narration or r.opening_narration

    @property
    def round_history(self) -> list[str]:
        return [r.summary for r in self._state.rounds if r.summary]

    # ── 公开 API ──

    def build_enter_prompt(self) -> str:
        """构造进场提示词 —— 发送给 LLM 生成开场叙事 + 选项。

        返回完整的 system prompt 文本，调用方应将它作为 system message 发送。
        """
        encounter = self._state.encounter
        return build_combat_system_prompt(
            encounter,
            round_number=self._state.current_round + 1,
            escalation="",
        )

    def build_enter_user_prompt(self) -> str:
        """构造进场 user 消息 —— 与 build_enter_prompt 配对使用。"""
        encounter = self._state.encounter
        escalation = build_round_escalation(encounter, self._state.current_round + 1)
        return build_combat_turn_user_prompt(
            encounter,
            round_number=self._state.current_round + 1,
            round_history=self.round_history,
            investigators_state=self._state.investigators_state,
            escalation=escalation,
        )

    def parse_options(self, llm_output: str) -> tuple[str, list[CombatOption]]:
        """从 LLM 输出中解析开场叙事和三个选项。

        参数:
            llm_output: LLM 的完整输出文本

        返回:
            (narration, options) — 开场叙事文本和三个赌注式选项
        """
        narration = ""
        options: list[CombatOption] = []
        option_keys = ["A", "B", "C"]

        # 按 --- 分割
        parts = llm_output.split("---")
        if not parts:
            return ("", [])

        # 第一部分是开场叙事（可能包含选项之前的全部文本）
        first_part = parts[0].strip()
        narration = first_part

        # 后续部分是选项
        for i, part in enumerate(parts[1:], 0):
            if i >= 3:
                break
            text = part.strip()
            if not text:
                continue

            key = option_keys[i] if i < len(option_keys) else "?"
            # 尝试提取标题（**粗体** 文本的第一行）
            label = text.split("\n")[0].strip().lstrip("*").rstrip("*").strip()
            if not label:
                label = f"选项{key}"

            options.append(CombatOption(
                label=label,
                description=text,
                option_key=key,
            ))

        return narration, options

    def start_round(self, llm_output: str) -> CombatRoundState:
        """开始新回合 —— 用 LLM 输出初始化回合状态。

        参数:
            llm_output: LLM 对 build_enter_prompt + build_enter_user_prompt 的回复

        返回:
            本回合的 CombatRoundState（供调用方展示给用户）
        """
        self._state.current_round += 1
        narration, options = self.parse_options(llm_output)

        round_state = CombatRoundState(
            round_number=self._state.current_round,
            opening_narration=narration,
            options=options,
        )
        self._state.rounds.append(round_state)
        self._state.phase = "voting"

        return round_state

    def submit_vote(self, chosen_key: str) -> CombatOption | None:
        """提交投票结果。

        参数:
            chosen_key: 被选中的选项标识 ("A" / "B" / "C")

        返回:
            被选中的 CombatOption，如果 key 无效则返回 None
        """
        if not self._state.rounds:
            self._state.error = "没有活跃的回合"
            return None

        round_state = self._state.rounds[-1]
        for opt in round_state.options:
            if opt.option_key.upper() == chosen_key.upper():
                round_state.chosen_option_key = chosen_key.upper()
                round_state.chosen_option = opt
                return opt

        self._state.error = f"无效的选项键: {chosen_key}"
        return None

    def build_resolve_prompt(self) -> str:
        """构造结算提示词 —— 用于请求 LLM 叙述行动结果。

        返回完整的 system prompt 文本。
        """
        encounter = self._state.encounter
        round_state = self._state.rounds[-1] if self._state.rounds else None
        if not round_state or not round_state.chosen_option:
            return ""

        return build_combat_system_prompt(
            encounter,
            round_number=self._state.current_round,
            escalation="",
        )

    def build_resolve_user_prompt(self) -> str:
        """构造结算 user 消息。"""
        encounter = self._state.encounter
        round_state = self._state.rounds[-1] if self._state.rounds else None
        if not round_state or not round_state.chosen_option:
            return ""

        return build_combat_resolution_prompt(
            encounter,
            chosen_option=round_state.chosen_option.description,
            round_number=self._state.current_round,
            action_description="",
            investigators_state=self._state.investigators_state,
        )

    def resolve(self, llm_output: str) -> CombatOutcome | None:
        """结算当前回合 —— 用 LLM 叙述行动结果并检查结局。

        参数:
            llm_output: LLM 对 resolve prompt 的回复

        返回:
            None 表示战斗继续（调用方应调用 start_round 开始下一轮）
            CombatOutcome 表示战斗结束
        """
        if not self._state.rounds:
            self._state.error = "没有活跃的回合"
            return None

        round_state = self._state.rounds[-1]
        round_state.resolution_narration = llm_output

        # 生成回合摘要
        summary = f"{round_state.chosen_option.label if round_state.chosen_option else '行动'} → "
        # 取结算叙事的第一句作为摘要
        first_line = llm_output.split("\n")[0].strip().rstrip("。").rstrip(".")
        summary += first_line[:80]
        round_state.summary = summary

        # 检查结局条件
        encounter = self._state.encounter
        outcome_id = encounter.check_outcome()

        if outcome_id:
            outcome = encounter.outcomes.get(outcome_id)
            if outcome:
                self._state.phase = "end"
                self._state.outcome = outcome
                self._state.outcome_id = outcome_id
                return outcome

        # 战斗继续
        self._state.phase = "enter"
        return None

    def end_summary(self) -> str:
        """生成战斗结束的叙事摘要。"""
        if not self._state.outcome:
            return "战斗异常结束。"

        return build_combat_outcome_summary(self._state.outcome)

    def get_vote_format(self) -> str:
        """生成投票展示格式 —— 供前端的弹幕投票模块使用。

        返回:
            格式化的投票文案，包含开场叙事 + 三个选项
        """
        if not self._state.rounds:
            return ""

        r = self._state.rounds[-1]
        lines = [r.opening_narration, ""]

        for opt in r.options:
            lines.append(f"[{opt.option_key}] {opt.label}")
            # 提取描述的第一行（代价预览）
            desc_lines = opt.description.strip().split("\n")
            if len(desc_lines) > 1:
                # 跳过标题行，取下一行作为预览
                lines.append(f"    {desc_lines[1].strip()[:100]}")

        return "\n".join(lines)
