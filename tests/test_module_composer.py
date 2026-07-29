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
        assert count >= 8, f"期望至少 8 个模块，实际 {count}"
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

        adv, seed = composer.compose(
            seed=42, max_depth=3, start_module="library_research",
        )

        # 找到 library_research 的最后场景
        lib_last = adv.get_scene("library_research::library")
        assert lib_last is not None

        # library 应有至少 2 条投票出口（exit_labels）
        exits = adv.scene_exits("library_research::library", include_locked=True)
        assert len(exits) >= 2, (
            f"library 应有至少 2 条投票出口，实际 exits={exits}"
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
        """验证显式剧情出口仍保留门控。"""
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(
            seed=42, max_depth=4, start_module="library_research",
        )
        scene_id = "library_research::library_converge"
        lib = adv.get_scene(scene_id)
        assert lib is not None

        exits = adv.scene_exits(scene_id, include_locked=True)
        targets = {
            adv.get_scene(e.target_id).leads_to[0]
            for e in exits
            if adv.get_scene(e.target_id) is not None and adv.get_scene(e.target_id).leads_to
        }
        # 手写的显式跳转必须始终存在；组合引擎现在还会叠加随机兼容候选作为额外分支，
        # 因此这里只断言手写目标是实际目标的子集，而非精确相等。
        assert {
            "basement_confrontation::basement",
            "museum_archives::archive_room",
            "sanitarium_visit::ward14",
        } <= targets

        required_ids = {e.required_element for e in exits if e.required_element}
        assert required_ids == {"l1", "l2"}

        for exit_info in exits:
            required = exit_info.required_element
            if not required:
                continue
            # 未解决门控元素时不可通过
            assert not adv.can_move_to(scene_id, exit_info.target_id)
            # 解决后可通过
            assert adv.can_move_to(
                scene_id, exit_info.target_id,
                resolved_ids={required},
            )

    def test_authored_external_destinations_are_preserved(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(
            seed=42, max_depth=3, start_module="hospital_morgue",
        )

        exits = adv.scene_exits("hospital_morgue::morgue_converge", include_locked=True)
        targets = {
            adv.get_scene(e.target_id).leads_to[0]
            for e in exits
            if adv.get_scene(e.target_id) is not None and adv.get_scene(e.target_id).leads_to
        }
        # 手写目标必须始终保留；随机兼容匹配可能叠加额外候选，因此用子集断言。
        assert {
            "foyer_investigation::foyer",
            "docks_warehouse::warehouse_ext",
            "sanitarium_visit::ward14",
        } <= targets

    def test_starting_branch_module_keeps_both_exits(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(
            seed=42, max_depth=3, start_module="library_research",
        )

        lib = adv.get_scene("library_research::library")
        assert lib is not None
        # library 场景内部 leads_to 指向模块内的子场景
        assert any("library_restricted" in target for target in lib.leads_to)
        assert any("library_converge" in target for target in lib.leads_to)

    def test_mood_variants_are_applied_to_composed_scenes(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, seed = composer.compose(
            seed=42, max_depth=3, start_module="foyer_investigation",
        )

        foyer = adv.get_scene("foyer_investigation::foyer")
        assert foyer is not None
        details = seed.mood_choices.get("foyer", [])
        assert details
        for detail in details:
            assert detail in foyer.description

    def test_npc_variants_are_applied_to_composed_adventure(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(
            seed=42, max_depth=3, start_module="library_research",
        )

        npc = adv.get_npc("老管理员")
        assert npc is not None
        assert npc.attitude == "neutral"
        assert "早已放弃了希望" in npc.description
        assert "不存在的人" in npc.secret

    def test_summary_reports_all_branch_points(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(seed=42, max_depth=3)

        assert "分支点" in adv.summary

    def test_max_depth_caps_module_layers(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(
            seed=42, max_depth=1, start_module="library_research",
        )

        module_ids = {
            sid.split("::", 1)[0]
            for sid in adv._scenes
            if "::" in sid
        }
        assert module_ids == {"library_research"}

    def test_internal_vote_labels_survive_scene_prefixing(self):
        composer = ModuleComposer(MODULES_DIR)
        composer.load_all()

        adv, _seed = composer.compose(
            seed=42, max_depth=3, start_module="dockside_arrival",
        )

        scene = adv.get_scene("dockside_arrival::docks")
        assert scene is not None
        labels = [e.label for e in adv.scene_exits("dockside_arrival::docks", include_locked=True)]
        assert "靠近渔船检查（有风险）" in labels
        assert "直接问水手（安全）" in labels
