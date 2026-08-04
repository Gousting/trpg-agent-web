"""T8 生化副本分支出口图测试。

覆盖：
- 生化副本完整出口图（hub → entrance → corridor/lab/boss）
- 三个主线模块各有 3 个真实出口（主线+分支）
- 分支模块能正确回到主线（armory/monitor/canteen → corridor；autopsy/dorm → lab）
- 通风管道跳层捷径（vent → lab，跳过 corridor 且满足 lab 的线索要求）
- BOSS 三结局（victory/defeat/flee）均回 hub
- 无幽灵分支：所有边都有 authored exit_labels 支撑
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trpg_agent.adventure.module_composer import ModuleComposer

INFINITE_DIR = str(Path(__file__).resolve().parents[1] / "data" / "modules_infinite_flow")


def _compile_rs(composer, seed=5, max_depth=7):
    """从 hub 编译，只收集生化链的场景图。"""
    bundle = composer.compile(
        seed=seed, start_module="hub_plaza", authored_only=True, max_depth=max_depth
    )
    scenes = bundle.adventure._scenes
    return scenes


def _scene_targets(rs_scenes, scene_key):
    """返回某场景 leads_to 的实际目标模块 id 列表（去掉 __trans__ 中间层）。"""
    leads = rs_scenes[scene_key].leads_to or []
    targets = []
    for lt in leads:
        if "__trans__" in lt:
            dest = rs_scenes[lt].leads_to or []
            targets.extend(dest)
        else:
            targets.append(lt)
    # 归一化为模块 id（去掉 ::scene 后缀）
    return sorted({t.split("::")[0] for t in targets if t})


class TestRsDungeonGraph:
    def setup_method(self):
        self.composer = ModuleComposer(INFINITE_DIR)
        self.composer.load_all()

    def test_entrance_three_exits(self):
        rs = _compile_rs(self.composer)
        targets = _scene_targets(rs, "dungeon_rs_entrance::tram")
        assert targets == ["dungeon_rs_armory", "dungeon_rs_corridor", "dungeon_rs_vent"], targets

    def test_corridor_three_exits(self):
        rs = _compile_rs(self.composer)
        targets = _scene_targets(rs, "dungeon_rs_corridor::corridor")
        assert targets == ["dungeon_rs_canteen", "dungeon_rs_lab", "dungeon_rs_monitor"], targets

    def test_lab_three_exits(self):
        rs = _compile_rs(self.composer)
        targets = _scene_targets(rs, "dungeon_rs_lab::lab")
        assert targets == ["dungeon_rs_autopsy", "dungeon_rs_boss", "dungeon_rs_dorm"], targets

    def test_branch_modules_return_to_mainline(self):
        rs = _compile_rs(self.composer)
        # 前置分支回 corridor
        assert _scene_targets(rs, "dungeon_rs_armory::armory") == ["dungeon_rs_corridor"]
        assert _scene_targets(rs, "dungeon_rs_monitor::monitor") == ["dungeon_rs_corridor"]
        assert _scene_targets(rs, "dungeon_rs_canteen::canteen") == ["dungeon_rs_corridor"]
        # 深层分支回 lab
        assert _scene_targets(rs, "dungeon_rs_autopsy::autopsy") == ["dungeon_rs_lab"]
        assert _scene_targets(rs, "dungeon_rs_dorm::dorm") == ["dungeon_rs_lab"]

    def test_vent_shortcut_jumps_to_lab(self):
        rs = _compile_rs(self.composer)
        targets = _scene_targets(rs, "dungeon_rs_vent::vent")
        # 跳层捷径：直达 lab；同时保留回退到 corridor 的出口
        assert "dungeon_rs_lab" in targets, targets
        assert "dungeon_rs_corridor" in targets, targets

    def test_boss_three_outcomes_return_hub(self):
        rs = _compile_rs(self.composer)
        for outcome in ["combat_victory", "combat_defeat", "combat_flee"]:
            key = f"dungeon_rs_boss::{outcome}"
            targets = _scene_targets(rs, key)
            assert "hub_plaza" in targets, f"{outcome} 应回 hub，实际 {targets}"

    def test_no_ghost_branches(self):
        """所有 rs 场景的出口目标都必须是真实存在的模块 id。"""
        rs = _compile_rs(self.composer)
        known = set(self.composer.module_ids())
        for key, scene in rs.items():
            for lt in scene.leads_to or []:
                if "__trans__" in lt:
                    dests = rs[lt].leads_to or []
                    for d in dests:
                        mod = d.split("::")[0]
                        assert mod in known, f"{key} -> {mod} 是幽灵分支（模块不存在）"
                else:
                    mod = lt.split("::")[0]
                    assert mod in known, f"{key} -> {mod} 是幽灵分支（模块不存在）"

    def test_difficulty_ladder(self):
        """生化难度阶梯：entrance 2 → corridor 2 → lab 3 → boss 3。"""
        metas = {m.meta.id: m for m in self.composer._modules.values()}
        diffs = {
            "dungeon_rs_entrance": metas["dungeon_rs_entrance"].meta.difficulty,
            "dungeon_rs_corridor": metas["dungeon_rs_corridor"].meta.difficulty,
            "dungeon_rs_lab": metas["dungeon_rs_lab"].meta.difficulty,
            "dungeon_rs_boss": metas["dungeon_rs_boss"].meta.difficulty,
        }
        assert diffs["dungeon_rs_entrance"] == 2, diffs
        assert diffs["dungeon_rs_corridor"] == 2, diffs
        assert diffs["dungeon_rs_lab"] == 3, diffs
        assert diffs["dungeon_rs_boss"] == 3, diffs
        assert diffs["dungeon_rs_boss"] >= diffs["dungeon_rs_lab"] >= diffs["dungeon_rs_corridor"]
