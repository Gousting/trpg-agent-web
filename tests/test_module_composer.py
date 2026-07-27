"""模块组合器测试。"""

from pathlib import Path

from trpg_agent.adventure import Adventure
from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.adventure.variance import RunSeed

MODULES_DIR = Path(__file__).resolve().parent.parent / "data" / "modules"


class TestModuleComposer:
    def test_load_all(self):
        composer = ModuleComposer(MODULES_DIR)
        count = composer.load_all()
        assert count >= 4, f"期望至少 4 个模块，实际 {count}"
        ids = composer.module_ids()
        assert "foyer_investigation" in ids
        assert "library_research" in ids
        assert "basement_confrontation" in ids
        assert "escape_chase" in ids

    def test_compose_basic(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(seed=42, max_depth=3)
        assert isinstance(adv, Adventure)
        assert isinstance(seed, RunSeed)
        assert len(adv._scenes) >= 3, f"场景数: {len(adv._scenes)}"
        assert adv.start_scene != ""
        start = adv.get_scene(adv.start_scene)
        assert start is not None

    def test_compose_with_branching(self):
        """library_research 有双出口，应产生分支。"""
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(seed=42, max_depth=3)

        # 找到 library_research 的最后场景
        lib_last = adv.get_scene("library_research::library")
        assert lib_last is not None

        # 应该有至少 2 个 leads_to（两个分支各一个过渡场景）
        normal_targets = [t for t in lib_last.leads_to if "__trans__" in t]
        assert len(normal_targets) >= 2, (
            f"library 应有至少 2 条出口，实际 leads_to={lib_last.leads_to}"
        )

        # 验证门控存在
        assert len(lib_last.exit_requires) >= 1, (
            f"应有 exit_requires 门控，实际: {lib_last.exit_requires}"
        )

    def test_scene_ids_prefixed(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(seed=42, max_depth=3)

        for sid in adv._scenes:
            if "__transition__" in sid or "__trans__" in sid:
                continue
            assert "::" in sid, f"场景 {sid} 缺少模块前缀"
            module_id, scene_id = sid.split("::", 1)
            assert module_id in composer.module_ids(), f"未知模块前缀: {module_id}"

    def test_reproducible(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv1, seed1 = composer.compose(seed=42, max_depth=3)
        adv2, seed2 = composer.compose(seed=42, max_depth=3)

        assert seed1.seed == seed2.seed
        assert adv1.title == adv2.title
        assert adv1.start_scene == adv2.start_scene
        assert len(adv1._scenes) == len(adv2._scenes)

    def test_adventure_block_works(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(seed=42, max_depth=3)

        block = adv.adventure_block(adv.start_scene)
        assert "冒险模组" in block
        assert "古屋" in block
        assert "当前场景" in block

    def test_can_move_to_works(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(seed=42, max_depth=3)

        start_id = adv.start_scene
        start_scene = adv.get_scene(start_id)
        assert start_scene is not None
        if start_scene.leads_to:
            next_id = start_scene.leads_to[0]
            assert adv.can_move_to(start_id, next_id)

    def test_start_module_forced(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(
            seed=99, max_depth=3, start_module="library_research",
        )
        assert adv.start_scene.startswith("library_research::")

    def test_difficulty_filter(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(
            seed=99, max_depth=3, difficulty_range=(1, 1),
        )
        assert adv.start_scene.startswith("foyer_investigation::")

    def test_branch_gating(self):
        """验证分支门控：解决 l2 才能去 trusted_path，解决 l1 才能去 alone_path。"""
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(seed=42, max_depth=3)
        lib = adv.get_scene("library_research::library")
        assert lib is not None

        # 门控键格式: {transition_scene_id: element_id}
        for trans_id, required in lib.exit_requires.items():
            assert required in ("l1", "l2"), (
                f"门控元素应为 l1 或 l2，实际: {required}"
            )
            # 未解决门控元素时不可通过
            assert not adv.can_move_to("library_research::library", trans_id)
            # 解决后可通过
            assert adv.can_move_to(
                "library_research::library", trans_id,
                resolved_ids={required},
            )
