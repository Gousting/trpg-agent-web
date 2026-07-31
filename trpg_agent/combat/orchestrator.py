"""战斗编排器 —— 将 web_server 中的 140 行战斗内联代码提取为独立模块。

CombatOrchestrator 管理战斗的完整生命周期，但把 SSE 推送、TTS、场景切换
等展示层逻辑留给调用方。调用方在每个阶段之间插入 SSE 事件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from trpg_agent.combat.encounter import CombatEncounter, CombatOutcome
from trpg_agent.combat.loop import CombatLoop, CombatRoundState
from trpg_agent.combat.resolver import CombatMechanicResult

log = logging.getLogger(__name__)


@dataclass
class CombatTurnResult:
    """一次完整战斗回合的输出——供 web_server 分发 SSE 事件。"""

    action: str                         # 玩家行动文本
    narration: str                      # 结算叙事（含结局摘要）
    outcome: CombatOutcome | None       # 非空表示战斗结束
    mech_result: CombatMechanicResult   # 机制结算结果


class CombatOrchestrator:
    """战斗编排器——封装战斗初始化、回合流转、结局跳转。

    用法:
        orch = CombatOrchestrator(session)
        # 每轮检测
        if orch.check_combat(adventure, session.state.scene_id):
            # → 进入战斗流程
            sys_prompt, usr_prompt = orch.prepare_enter()
            # → SSE: kp_stream_start → LLM 流式 → kp_stream_end
            round_state = orch.complete_enter(llm_output)
            # → SSE: vote 窗口
            mech_result = orch.submit_and_resolve_mechanics("A")
            # → SSE: player_token
            sys_p, usr_p = orch.prepare_resolve()
            # → SSE: kp_stream_start → LLM 流式
            result = orch.complete_turn(llm_output)
            # → 根据 result.outcome 判断是否结束
    """

    def __init__(self, investigators_state: str = "") -> None:
        self.combat_loop: CombatLoop | None = None
        self._investigators_state = investigators_state

    def check_combat(self, adventure, scene_id: str) -> bool:
        """检测当前场景是否为战斗场景。

        如果是且战斗循环尚未初始化，则创建 CombatLoop 并返回 True。
        如果战斗循环已存在（战斗中），直接返回 True。
        如果不是战斗场景，返回 False。
        """
        if self.combat_loop is not None:
            return True

        if adventure is None:
            return False

        scene = adventure.get_scene(scene_id)
        if scene is None or not scene.combat or not scene.combat.get("enabled"):
            return False

        encounter = scene.combat["encounter"]
        self.combat_loop = CombatLoop(
            encounter,
            investigators_state=self._investigators_state,
        )
        return True

    def prepare_enter(self) -> tuple[str, str]:
        """准备进场提示词——返回 (system_prompt, user_prompt)。"""
        if self.combat_loop is None:
            return "", ""
        return (
            self.combat_loop.build_enter_prompt(),
            self.combat_loop.build_enter_user_prompt(),
        )

    def complete_enter(self, llm_output: str) -> CombatRoundState:
        """解析 LLM 输出并进入投票阶段。"""
        if self.combat_loop is None:
            raise RuntimeError("CombatOrchestrator: combat_loop 未初始化")
        return self.combat_loop.start_round(llm_output)

    def get_vote_format(self) -> str:
        """获取投票展示文案。"""
        if self.combat_loop is None:
            return ""
        return self.combat_loop.get_vote_format()

    def submit_and_resolve_mechanics(self, choice: str) -> CombatMechanicResult:
        """提交投票并运行机制层——返回结构化结算结果。"""
        if self.combat_loop is None:
            raise RuntimeError("CombatOrchestrator: combat_loop 未初始化")
        self.combat_loop.submit_vote(choice.upper())
        return self.combat_loop.run_mechanics()

    def prepare_resolve(self) -> tuple[str, str]:
        """准备结算提示词——返回 (system_prompt, user_prompt)。

        必须在 submit_and_resolve_mechanics() 之后调用。
        """
        if self.combat_loop is None:
            return "", ""
        mech_result = self.combat_loop._last_mech_result
        return (
            self.combat_loop.build_resolve_prompt(),
            self.combat_loop.build_resolve_user_prompt(mech_result=mech_result),
        )

    def complete_turn(self, llm_output: str) -> CombatTurnResult:
        """完成回合结算——返回结构化结果供 web_server 分发 SSE。"""
        if self.combat_loop is None:
            raise RuntimeError("CombatOrchestrator: combat_loop 未初始化")

        mech_result = self.combat_loop._last_mech_result
        if mech_result is None:
            mech_result = CombatMechanicResult(
                success=False, test_rolled=False, test_result=None,
                skill=None, difficulty="", target=0, summary="",
            )

        outcome = self.combat_loop.resolve(llm_output, mech_result=mech_result)

        round_state = self.combat_loop._state.rounds[-1] if self.combat_loop._state.rounds else None
        action = ""
        if round_state and round_state.chosen_option:
            action = f"（全员选择了「{round_state.chosen_option.label}」）"

        narration = llm_output
        if outcome is not None:
            narration += "\n\n" + self.combat_loop.end_summary()

        return CombatTurnResult(
            action=action,
            narration=narration,
            outcome=outcome,
            mech_result=mech_result,
        )

    def force_end_if_needed(self, max_rounds: int = 6) -> CombatOutcome | None:
        """回合数超限时强制结束战斗。返回结局或 None。"""
        if self.combat_loop is None:
            return None
        if self.combat_loop.current_round < max_rounds:
            return None
        encounter = self.combat_loop._state.encounter
        fallback_id = next(
            (oid for oid in ("flee", "defeat", "victory") if oid in encounter.outcomes),
            next(iter(encounter.outcomes), ""),
        )
        if fallback_id:
            return self.combat_loop.force_end(fallback_id)
        return None

    def combat_summary(self) -> str:
        """战斗摘要——供 session.record_combat() 使用。"""
        if self.combat_loop is None:
            return ""
        return self.combat_loop.combat_summary

    def scene_transition_info(
        self, current_combat_scene
    ) -> tuple[str, str] | None:
        """返回战斗结束后的跳转信息 (module_id, target_scene_id)。

        参数:
            current_combat_scene: 当前战斗场景对象（需要有 id 属性）

        返回:
            (module_id, target_scene_id) 或 None（战斗未结束或无结局）
        """
        if self.combat_loop is None or self.combat_loop.outcome is None:
            return None
        module_id = current_combat_scene.id.split("::", 1)[0]
        target_id = CombatEncounter.outcome_scene_id(module_id, self.combat_loop.outcome.id)
        return (module_id, target_id)

    def reset(self) -> None:
        """重置编排器状态（战斗结束后调用）。"""
        self.combat_loop = None
