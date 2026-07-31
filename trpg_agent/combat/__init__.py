"""战斗模块系统 —— 独立遭遇 + 回合制引擎 + 代码驱动机制层 + 叙事桥接。"""

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
    build_narration_prompt,
)

from trpg_agent.combat.loop import (
    CombatLoop,
    CombatState,
    CombatRoundState,
    CombatOption,
)

from trpg_agent.combat.resolver import (
    CombatMechanics,
    CombatMechanicResult,
)

from trpg_agent.combat.orchestrator import (
    CombatOrchestrator,
    CombatTurnResult,
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
    "CombatMechanics",
    "CombatMechanicResult",
    "CombatOrchestrator",
    "CombatTurnResult",
    "build_combat_system_prompt",
    "build_combat_turn_user_prompt",
    "build_combat_resolution_prompt",
    "build_combat_outcome_summary",
    "build_round_escalation",
    "build_narration_prompt",
]
