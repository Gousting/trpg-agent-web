"""T9 修仙副本分支出口图测试。

覆盖：
- 修仙副本完整出口图（hub → entrance → trial → danfang → boss）
- 三个主线模块各有 3 个真实出口（主线+分支）
- 分支模块能正确回到主线（library/field → trial；arena/mentor/court → danfang）
- 后山禁地跳层捷径（forbidden → boss，绕过 danfang 主线出口）
- BOSS 三结局（victory/defeat/flee）均回 hub
- 无幽灵分支：所有边都有 authored exit_labels 支撑
- 难度阶梯 3-4：entrance 3 → trial 3 → danfang 4 → boss 4
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trpg_agent.adventure.module_composer import ModuleComposer

INFINITE_DIR = str(Path(__file__).resolve().parents[1] / "data" / "modules_infinite_flow")


def _compile_xt(composer, seed=11, max_depth=9):
    bundle = composer.compile(
        seed=seed, start_module="hub_plaza", authored_only=True, max_depth=max_depth
    )
    return bundle.adventure._scenes


def _scene_targets(scenes, scene_key):
    leads = scenes[scene_key].leads_to or []
    targets = []
    for lt in leads:
        if "__trans__" in lt:
            dest = scenes[lt].leads_to or []
            targets.extend(dest)
        else:
            targets.append(lt)
    return sorted({t.split("::")[0] for t in targets if t})


class TestXtDungeonGraph:
    def setup_method(self):
        self.composer = ModuleComposer(INFINITE_DIR)
        self.composer.load_all()

    def test_entrance_three_exits(self):
        scenes = _compile_xt(self.composer)
        targets = _scene_targets(scenes, "dungeon_xiuxian_entrance::mountain_gate")
        assert targets == ["dungeon_xiuxian_field", "dungeon_xiuxian_library", "dungeon_xiuxian_trial", "hub_plaza"], targets

    def test_trial_three_exits(self):
        scenes = _compile_xt(self.composer)
        targets = _scene_targets(scenes, "dungeon_xiuxian_trial::trial_field")
        assert targets == ["dungeon_xiuxian_arena", "dungeon_xiuxian_danfang", "dungeon_xiuxian_mentor", "hub_plaza"], targets

    def test_danfang_three_exits(self):
        scenes = _compile_xt(self.composer)
        targets = _scene_targets(scenes, "dungeon_xiuxian_danfang::danfang")
        assert targets == ["dungeon_xiuxian_boss", "dungeon_xiuxian_court", "dungeon_xiuxian_forbidden", "hub_plaza"], targets

    def test_branch_modules_return_to_mainline(self):
        scenes = _compile_xt(self.composer)
        # 前置分支回 trial（arena 已是 combat 遭遇战，单独断言）
        assert _scene_targets(scenes, "dungeon_xiuxian_library::library") == ["dungeon_xiuxian_trial", "hub_plaza"]
        assert _scene_targets(scenes, "dungeon_xiuxian_field::field") == ["dungeon_xiuxian_trial", "hub_plaza"]
        # 深层分支回 danfang
        assert _scene_targets(scenes, "dungeon_xiuxian_mentor::mentor_hall") == ["dungeon_xiuxian_danfang", "hub_plaza"]
        assert _scene_targets(scenes, "dungeon_xiuxian_court::court") == ["dungeon_xiuxian_danfang", "hub_plaza"]
        # combat 遭遇战（arena）：胜利回主线（同副本模块），失败/逃跑回 hub
        v_targets = _scene_targets(scenes, "dungeon_xiuxian_arena::combat_victory")
        assert v_targets and "hub_plaza" not in v_targets, f"胜利应回主线: {v_targets}"
        assert _scene_targets(scenes, "dungeon_xiuxian_arena::combat_defeat") == ["hub_plaza"]
        assert _scene_targets(scenes, "dungeon_xiuxian_arena::combat_flee") == ["hub_plaza"]

    def test_forbidden_shortcut_jumps_to_boss(self):
        scenes = _compile_xt(self.composer)
        targets = _scene_targets(scenes, "dungeon_xiuxian_forbidden::forbidden")
        assert "dungeon_xiuxian_boss" in targets, targets
        assert "dungeon_xiuxian_danfang" in targets, targets

    def test_forbidden_shortcut_stable_across_seeds(self):
        """跳层捷径不能只在一个 seed 下成立（曾因 used_ids 去重丢失 boss 出口）。"""
        for seed in [3, 11, 22, 77]:
            scenes = _compile_xt(self.composer, seed=seed)
            targets = _scene_targets(scenes, "dungeon_xiuxian_forbidden::forbidden")
            assert "dungeon_xiuxian_boss" in targets, f"seed={seed} 丢失跳层出口: {targets}"

    def test_boss_three_outcomes_return_hub(self):
        scenes = _compile_xt(self.composer)
        for outcome in ["combat_victory", "combat_defeat", "combat_flee"]:
            key = f"dungeon_xiuxian_boss::{outcome}"
            targets = _scene_targets(scenes, key)
            assert "hub_plaza" in targets, f"{outcome} 应回 hub，实际 {targets}"

    def test_no_ghost_branches(self):
        scenes = _compile_xt(self.composer)
        known = set(self.composer.module_ids())
        for key, scene in scenes.items():
            for lt in scene.leads_to or []:
                if "__trans__" in lt:
                    dests = scenes[lt].leads_to or []
                    for d in dests:
                        mod = d.split("::")[0]
                        assert mod in known, f"{key} -> {mod} 是幽灵分支"
                else:
                    mod = lt.split("::")[0]
                    assert mod in known, f"{key} -> {mod} 是幽灵分支"

    def test_difficulty_ladder(self):
        """修仙难度阶梯：entrance 3 → trial 3 → danfang 4 → boss 4。"""
        metas = {m.meta.id: m for m in self.composer._modules.values()}
        diffs = {
            "dungeon_xiuxian_entrance": metas["dungeon_xiuxian_entrance"].meta.difficulty,
            "dungeon_xiuxian_trial": metas["dungeon_xiuxian_trial"].meta.difficulty,
            "dungeon_xiuxian_danfang": metas["dungeon_xiuxian_danfang"].meta.difficulty,
            "dungeon_xiuxian_boss": metas["dungeon_xiuxian_boss"].meta.difficulty,
        }
        assert diffs["dungeon_xiuxian_entrance"] == 3, diffs
        assert diffs["dungeon_xiuxian_trial"] == 3, diffs
        assert diffs["dungeon_xiuxian_danfang"] == 4, diffs
        assert diffs["dungeon_xiuxian_boss"] == 4, diffs
        assert diffs["dungeon_xiuxian_boss"] >= diffs["dungeon_xiuxian_danfang"] >= diffs["dungeon_xiuxian_trial"]
