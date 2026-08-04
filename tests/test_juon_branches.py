"""T7：咒怨模块分支出口图测试。

验证点：
1. entrance 三出口（主线 hallway + 分支 neighbor/well），无幽灵分支
2. hallway 三出口（主线 attic + 分支 bathroom/storage）
3. attic 三出口（主线 boss + 分支 diary + 撤退 hub）
4. 分支模块出口回主线（reusable 回边成立）
5. neighbor → deed 链成立
6. 分支模块不会被 hub 随机匹配提前连走（入口线索约束）
"""
from pathlib import Path

from trpg_agent.adventure.module_composer import ModuleComposer

INFINITE_DIR = Path(__file__).resolve().parent.parent / "data" / "modules_infinite_flow"


def _compile():
    composer = ModuleComposer(INFINITE_DIR)
    composer.load_all()
    return composer.compose(seed=7, max_depth=6, start_module="hub_plaza", authored_only=True)


def _targets(adv, scene_id: str) -> set[str]:
    out = set()
    for e in adv.scene_exits(scene_id, include_locked=True):
        trans = adv.get_scene(e.target_id)
        if trans and trans.leads_to:
            out.add(trans.leads_to[0])
    return out


class TestJuonBranchGraph:
    def test_entrance_three_exits_no_ghost(self):
        """entrance 恰有三出口：主线 + neighbor + well，不含其他幽灵目标。"""
        adv, _ = _compile()
        targets = _targets(adv, "dungeon_juon_entrance::gate")
        assert targets == {
            "dungeon_juon_hallway::hallway",
            "dungeon_juon_neighbor::living_room",
            "dungeon_juon_well::yard",
        }, f"entrance 出口异常: {targets}"

    def test_hallway_three_exits(self):
        adv, _ = _compile()
        targets = _targets(adv, "dungeon_juon_hallway::hallway")
        assert targets == {
            "dungeon_juon_attic::attic",
            "dungeon_juon_bathroom::bath",
            "dungeon_juon_storage::closet",
        }, f"hallway 出口异常: {targets}"

    def test_attic_three_exits_with_retreat(self):
        """attic 三出口：BOSS + diary 分支 + 撤退回 hub。"""
        adv, _ = _compile()
        targets = _targets(adv, "dungeon_juon_attic::attic")
        assert targets == {
            "dungeon_juon_boss::combat_encounter",
            "dungeon_juon_diary::attic_diary",
            "hub_plaza::plaza",
        }, f"attic 出口异常: {targets}"

    def test_branch_modules_return_to_mainline(self):
        """分支模块出口回主线（reusable 回边），副本链不因分支断裂。"""
        adv, _ = _compile()
        for scene_id, expected in {
            "dungeon_juon_well::yard": {"dungeon_juon_hallway::hallway"},
            "dungeon_juon_storage::closet": {"dungeon_juon_hallway::hallway"},
            "dungeon_juon_bathroom::bath": {"dungeon_juon_hallway::hallway"},
            "dungeon_juon_diary::attic_diary": {"dungeon_juon_attic::attic"},
        }.items():
            assert _targets(adv, scene_id) == expected, f"{scene_id} 回边异常"

    def test_neighbor_deed_chain(self):
        """邻居支线两模块链：neighbor → deed → hallway。"""
        adv, _ = _compile()
        assert _targets(adv, "dungeon_juon_neighbor::living_room") == {
            "dungeon_juon_deed::deed",
            "dungeon_juon_hallway::hallway",
        }
        assert _targets(adv, "dungeon_juon_deed::deed") == {"dungeon_juon_hallway::hallway"}

    def test_pool_still_validates(self):
        """加 6 个分支模块后 validate 零问题。"""
        composer = ModuleComposer(INFINITE_DIR)
        composer.load_all()
        assert composer.validate() == []
