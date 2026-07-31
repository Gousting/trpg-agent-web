"""战斗模块系统 —— 独立遭遇 + 回合制引擎 + 叙事桥接。"""

from trpg_agent.combat.encounter import (
    CombatEncounter,
    CombatEnvironment,
    CombatOutcome,
    Enemy,
)

from trpg_agent.combat.prompts import (
    build_combat_system_prompt,
    build_combat_turn_user_prompt,
    build_combat_resolution_prompt,
    build_combat_outcome_summary,
    build_round_escalation,
)

from trpg_agent.combat.loop import (
    CombatLoop,
    CombatState,
    CombatRoundState,
    CombatOption,
)

__all__ = [
    "CombatEncounter",
    "CombatEnvironment",
    "CombatOutcome",
    "Enemy",
    "CombatLoop",
    "CombatState",
    "CombatRoundState",
    "CombatOption",
    "build_combat_system_prompt",
    "build_combat_turn_user_prompt",
    "build_combat_resolution_prompt",
    "build_combat_outcome_summary",
    "build_round_escalation",
]
