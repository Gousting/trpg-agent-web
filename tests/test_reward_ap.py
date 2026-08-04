"""T2：收益结构化——BOSS outcomes reward_ap 解析与结算数据测试。

验证点：
1. CombatOutcome.from_dict 解析 reward_ap（含缺省为 0，兼容 COC 模块）
2. 三个无限流 BOSS 模块 JSON 均声明 reward_ap（victory 4 / defeat 1 / flee 0）
3. CombatEncounter.from_dict 透传 outcomes 的 reward_ap
"""
import json
from pathlib import Path

from trpg_agent.combat.encounter import CombatEncounter, CombatOutcome

INFINITE_DIR = Path(__file__).resolve().parent.parent / "data" / "modules_infinite_flow"
BOSS_IDS = ["dungeon_juon_boss", "dungeon_rs_boss", "dungeon_xiuxian_boss"]
EXPECTED_AP = {"victory": 4, "defeat": 1, "flee": 0}


class TestRewardAp:
    def test_combat_outcome_parses_reward_ap(self):
        oc = CombatOutcome.from_dict({"label": "胜利", "reward": "x4", "reward_ap": 4})
        assert oc.reward_ap == 4

    def test_combat_outcome_defaults_to_zero(self):
        """COC 模块不写 reward_ap → 默认 0，不影响结算兜底逻辑。"""
        oc = CombatOutcome.from_dict({"label": "胜利"})
        assert oc.reward_ap == 0

    def test_encounter_parses_outcome_reward_ap(self):
        enc = CombatEncounter.from_dict({
            "id": "t",
            "outcomes": {
                "victory": {"label": "V", "reward_ap": 4},
                "flee": {"label": "F"},
            },
        })
        assert enc.outcomes["victory"].reward_ap == 4
        assert enc.outcomes["flee"].reward_ap == 0

    def test_all_boss_modules_declare_reward_ap(self):
        """三个 BOSS 模块的 reward_ap 与设计文档 §4 一致。"""
        for boss_id in BOSS_IDS:
            path = INFINITE_DIR / boss_id / "module.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            outcomes = data["outcomes"]
            assert set(outcomes.keys()) == set(EXPECTED_AP.keys()), f"{boss_id} 结局集合异常"
            for key, ap in EXPECTED_AP.items():
                assert outcomes[key].get("reward_ap") == ap, f"{boss_id} {key} reward_ap 期望 {ap}"
