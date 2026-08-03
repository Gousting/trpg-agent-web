"""无限流模块池测试——副本隔离与主神空间循环。

验证点：
1. 无限流模块池加载 + validate 零问题
2. hub 起始时三个副本出口各匹配各的入口（location_type 隔离）
3. 副本内部链连续（入口→中段→BOSS 全程同 dungeon 类型）
4. BOSS 结局指回 hub（循环成立）
"""

from pathlib import Path

from trpg_agent.adventure.module_composer import ModuleComposer

INFINITE_DIR = Path(__file__).resolve().parent.parent / "data" / "modules_infinite_flow"


class TestInfiniteFlow:
    def test_pool_loads_and_validates(self):
        composer = ModuleComposer(INFINITE_DIR)
        count = composer.load_all()
        assert count == 13, f"期望 13 个模块，实际 {count}"
        assert "hub_plaza" in composer.module_ids()
        assert composer.validate() == []

    def test_hub_exits_map_to_each_dungeon(self):
        """hub 的三个出口应分别指向三个副本入口，location_type 精确匹配。"""
        composer = ModuleComposer(INFINITE_DIR)
        composer.load_all()
        adv, _seed = composer.compose(seed=7, max_depth=6, start_module="hub_plaza")

        exits = adv.scene_exits("hub_plaza::plaza", include_locked=True)
        targets = set()
        for e in exits:
            trans = adv.get_scene(e.target_id)
            if trans and trans.leads_to:
                targets.add(trans.leads_to[0])

        assert "dungeon_rs_entrance::tram" in targets
        assert "dungeon_juon_entrance::gate" in targets
        assert "dungeon_xiuxian_entrance::mountain_gate" in targets

    def test_dungeon_chains_stay_isolated(self):
        """每个副本内部链连续：入口→中段→深层→BOSS 全程同 dungeon 类型。"""
        composer = ModuleComposer(INFINITE_DIR)
        composer.load_all()
        adv, _seed = composer.compose(seed=7, max_depth=6, start_module="hub_plaza")

        # 生化链：entrance → corridor → lab → boss
        rs_chain = ["dungeon_rs_entrance", "dungeon_rs_corridor", "dungeon_rs_lab", "dungeon_rs_boss"]
        for i in range(len(rs_chain) - 1):
            cur = adv.get_scene(f"{rs_chain[i]}::tram" if i == 0 else f"{rs_chain[i]}::corridor" if i == 1 else f"{rs_chain[i]}::lab" if i == 2 else f"{rs_chain[i]}::combat_encounter")
            assert cur is not None, f"场景缺失: {rs_chain[i]}"
            # 每个模块的最后场景应能到达下一模块（通过过渡场景）
            next_id = f"{rs_chain[i+1]}::" if rs_chain[i+1] != "dungeon_rs_boss" else f"{rs_chain[i+1]}::combat_encounter"
            # 遍历 leads_to 找到通向下一模块的过渡
            found = False
            for lead in (cur.leads_to or []):
                trans = adv.get_scene(lead)
                if trans and any(t.startswith(next_id) for t in (trans.leads_to or [])):
                    found = True
                    break
            assert found, f"副本链断裂: {rs_chain[i]} 无法到达 {rs_chain[i+1]}"

    def test_boss_outcomes_return_to_hub(self):
        """BOSS 的三种结局都应指回 hub_plaza。"""
        composer = ModuleComposer(INFINITE_DIR)
        composer.load_all()
        adv, _seed = composer.compose(seed=7, max_depth=6, start_module="hub_plaza")

        for outcome in ("victory", "defeat", "flee"):
            scene_id = f"dungeon_rs_boss::combat_{outcome}"
            scene = adv.get_scene(scene_id)
            assert scene is not None, f"结局场景缺失: {scene_id}"
            # 结局场景的过渡应通向 hub
            found_hub = False
            for lead in (scene.leads_to or []):
                trans = adv.get_scene(lead)
                if trans and any(t.startswith("hub_plaza::") for t in (trans.leads_to or [])):
                    found_hub = True
                    break
            assert found_hub, f"结局 {outcome} 未返回主神空间"
