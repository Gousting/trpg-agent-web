"""T4: 模块机制差异化测试——rest 恢复 / trap 检定 / 模块数据完整性

覆盖：
- rest 模块进入时恢复轮回者 HP（播报 + 实际数值）
- trap 模块检定成功给线索 / 失败扣 HP
- 非 boss 模块都有 opportunities（选项补齐的数据源）
- combat 遭遇战模块编译后 enemies/exits 结构正确
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.memory.game_state import Reincarnator

INFINITE_DIR = Path(__file__).resolve().parents[1] / "data" / "modules_infinite_flow"


def _make_composer():
    c = ModuleComposer(INFINITE_DIR)
    c.load_all()
    return c


class _FakeState:
    """轻量 mock session.state——只含 _module_scene_effects 需要的字段。"""

    def __init__(self, rein=None, investigators=None):
        self.reincarnator = rein
        self.investigators = investigators or []
        self.resolved_elements = set()


class TestModuleEffects(unittest.TestCase):
    def setUp(self):
        self.composer = _make_composer()

    def _effects(self, module_id, state, is_infinite_flow=True):
        from trpg_agent_web.web_server import _module_scene_effects
        return _module_scene_effects(
            self.composer, None, f"{module_id}::scene", _FakeSession(state),
            is_infinite_flow,
        )

    def test_rest_recovers_reincarnator_hp(self):
        rein = Reincarnator(name="轮回者", max_hp=12, hp=5)
        texts = self._effects("dungeon_rs_canteen", _FakeState(rein=rein))
        self.assertTrue(texts, "rest 应有播报")
        self.assertGreater(rein.hp, 5, "进入食堂应恢复 HP")
        self.assertIn("食堂", texts[0])

    def test_rest_no_overheal(self):
        rein = Reincarnator(name="轮回者", max_hp=12, hp=12)
        texts = self._effects("dungeon_rs_dorm", _FakeState(rein=rein))
        self.assertEqual(rein.hp, 12, "满血时不应溢出")

    def test_trap_success_adds_clue(self):
        rein = Reincarnator(name="轮回者", max_hp=12, hp=12)
        state = _FakeState(rein=rein)
        # 强制检定成功：monkeypatch random.randint 返回 1（<= difficulty 11）
        import trpg_agent_web.web_server as ws
        orig = ws.random.randint
        ws.random.randint = lambda a, b: 1
        try:
            texts = self._effects("dungeon_rs_vent", state)
        finally:
            ws.random.randint = orig
        self.assertTrue(texts)
        self.assertIn("rs_vent_clue", state.resolved_elements, "成功应获得线索")
        self.assertEqual(rein.hp, 12, "成功不应扣血")

    def test_trap_failure_damages_hp(self):
        rein = Reincarnator(name="轮回者", max_hp=12, hp=12)
        state = _FakeState(rein=rein)
        import trpg_agent_web.web_server as ws
        orig = ws.random.randint
        ws.random.randint = lambda a, b: 20  # 必失败（> difficulty）
        try:
            texts = self._effects("dungeon_rs_vent", state)
        finally:
            ws.random.randint = orig
        self.assertTrue(texts)
        self.assertLess(rein.hp, 12, "失败应扣血（vent hp_loss=3 + san_loss=0）")
        self.assertNotIn("rs_vent_clue", state.resolved_elements)


class TestModuleData(unittest.TestCase):
    def setUp(self):
        self.composer = _make_composer()

    def test_all_non_boss_modules_have_opportunities(self):
        """选项补齐的数据源：每个非 boss 模块的场景至少 1 条 opportunity。"""
        for mod in self.composer._modules.values():
            if "boss" in mod.meta.id or mod.meta.id == "hub_plaza":
                continue
            with self.subTest(module=mod.meta.id):
                opps = [o.text for s in mod.scenes for o in s.opportunities if o.text]
                self.assertTrue(opps, f"{mod.meta.id} 无 opportunities")

    def test_combat_encounter_modules_have_enemies(self):
        """战斗遭遇模块（非 boss combat）必须有敌人和 victory/defeat/flee 出口。"""
        for mod in self.composer._modules.values():
            if mod.meta.module_type != "combat" or "boss" in mod.meta.id:
                continue
            with self.subTest(module=mod.meta.id):
                self.assertTrue(getattr(mod, "encounter", None) is not None, f"{mod.meta.id} 无 encounter")
                enc = mod.encounter
                self.assertTrue(getattr(enc, "enemies", []), f"{mod.meta.id} 无敌人")
                exit_ids = [e.id for e in mod.meta.exits]
                self.assertIn("victory", exit_ids)
                self.assertIn("defeat", exit_ids)
                self.assertIn("flee", exit_ids)

    def test_rest_trap_data_present(self):
        self.assertEqual(self.composer._modules["dungeon_rs_canteen"].meta.rest["hp_recover"], 4)
        self.assertEqual(self.composer._modules["dungeon_juon_well"].meta.trap["check"], "spirit")
        self.assertEqual(self.composer._modules["dungeon_xiuxian_forbidden"].meta.trap["difficulty"], 14)


class _FakeSession:
    def __init__(self, state):
        self.state = state


if __name__ == "__main__":
    unittest.main()
