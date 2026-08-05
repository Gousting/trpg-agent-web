"""新模块类型交互测试——puzzle/social/choice/interaction/BOSS 阶段。

覆盖：
- ModuleMeta 解析新字段（puzzle/social/choice/interaction/phase_thresholds）
- _module_interaction_options 生成选项（含已结算去重）
- _module_interaction_resolve 结算（谜题对错/社交收益/抉择代价/互动叙事）
- CombatEncounter.check_phase_triggers 阶段触发（狂暴攻击加值）
"""
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trpg_agent.adventure.module_composer import ModuleComposer, ModuleMeta
from trpg_agent.combat.encounter import CombatEncounter
from trpg_agent.combat.resolver import CombatMechanics
from trpg_agent.memory.game_state import GameState, Reincarnator

DATA = Path(__file__).resolve().parents[1] / "data" / "modules_infinite_flow"


@pytest.fixture()
def composer():
    c = ModuleComposer(DATA)
    c.load_all()
    return c


def _session_with_rein():
    """返回带轮回者的最小 session 替身（含 state.resolved_elements）。"""
    st = GameState()
    st.reincarnator = Reincarnator(name="轮回者", hp=10, max_hp=12)

    class _S:
        state = st
    return _S()


# ── ModuleMeta 解析 ────────────────────────────────────

class TestModuleMetaFields:
    @pytest.mark.parametrize("mid,field", [
        ("dungeon_juon_deed", "puzzle"),
        ("dungeon_rs_monitor", "puzzle"),
        ("dungeon_xiuxian_library", "puzzle"),
        ("dungeon_juon_neighbor", "social"),
        ("dungeon_rs_autopsy", "social"),
        ("dungeon_xiuxian_mentor", "social"),
        ("dungeon_juon_bathroom", "choice"),
        ("dungeon_rs_lab", "choice"),
        ("dungeon_xiuxian_court", "choice"),
        ("dungeon_juon_diary", "interaction"),
        ("dungeon_rs_dorm", "interaction"),
        ("dungeon_xiuxian_danfang", "interaction"),
    ])
    def test_module_type_and_field(self, composer, mid, field):
        mod = composer._modules[mid]
        if field == "interaction":
            # interaction 是模块的轻量互动增强：story 保持 story、rest 保持 rest，字段存在即可
            assert getattr(mod.meta, field), f"{mid} 应有 {field} 数据"
        else:
            assert mod.meta.module_type == field, f"{mid} 类型应为 {field}"
            assert getattr(mod.meta, field), f"{mid} 应有 {field} 数据"

    def test_boss_phase_thresholds(self, composer):
        for mid in ("dungeon_juon_boss", "dungeon_rs_boss", "dungeon_xiuxian_boss"):
            mod = composer._modules[mid]
            assert mod.meta.module_type == "combat"
            assert len(mod.meta.phase_thresholds) >= 2, f"{mid} 应有阶段阈值"
            for ph in mod.meta.phase_thresholds:
                assert 0 < float(ph["threshold"]) < 1
                assert int(ph.get("attack_bonus", 0)) > 0


# ── 交互选项生成 ───────────────────────────────────────

class TestInteractionOptions:
    def test_puzzle_options_generated(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_juon_deed::deed", s)
        assert opts is not None
        assert len(opts) == 3
        assert any(v["kind"] == "puzzle" and v["correct"] for v in opts.values())

    def test_resolved_mark_dedup(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options
        s = _session_with_rein()
        s.state.resolved_elements.add("dungeon_juon_deed_interacted")
        opts = _module_interaction_options(composer, None, "dungeon_juon_deed::deed", s)
        assert opts is None

    def test_story_interaction_generated(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_juon_diary::diary", s)
        assert opts is not None
        assert len(opts) == 3
        assert all(v["kind"] == "interaction" for v in opts.values())

    def test_plain_story_no_interaction(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options
        s = _session_with_rein()
        # attic 是纯 story 无 interaction 字段
        opts = _module_interaction_options(composer, None, "dungeon_juon_attic::attic", s)
        assert opts is None

    def test_rest_with_interaction_generated(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options
        s = _session_with_rein()
        # rs_dorm 是 rest + interaction 组合：rest 自动回血 + 互动选项并存
        opts = _module_interaction_options(composer, None, "dungeon_rs_dorm::dorm", s)
        assert opts is not None
        assert len(opts) == 3
        assert all(v["kind"] == "interaction" for v in opts.values())


# ── 交互结算 ───────────────────────────────────────────

class TestInteractionResolve:
    def test_puzzle_correct_gives_clue(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options, _module_interaction_resolve
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_juon_deed::deed", s)
        correct = next(v for v in opts.values() if v["correct"])
        texts = _module_interaction_resolve(correct, s, True)
        assert any("谜题破解" in t for t in texts)
        assert correct["clue"] in s.state.resolved_elements

    def test_puzzle_wrong_damages(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options, _module_interaction_resolve
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_rs_monitor::monitor", s)
        wrong = next(v for v in opts.values() if not v["correct"])
        hp_before = s.state.reincarnator.hp
        texts = _module_interaction_resolve(wrong, s, True)
        assert any("解谜失败" in t for t in texts)
        assert s.state.reincarnator.hp < hp_before

    def test_social_clue_effect(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options, _module_interaction_resolve
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_juon_neighbor::neighbor", s)
        clue_opt = next(v for v in opts.values() if v["effect_type"] == "clue")
        texts = _module_interaction_resolve(clue_opt, s, True)
        assert any("关键情报" in t for t in texts)
        assert clue_opt["clue"] in s.state.resolved_elements

    def test_choice_costs_and_clue(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options, _module_interaction_resolve
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_juon_bathroom::bathroom", s)
        opt = next(v for v in opts.values() if v["hp_cost"] > 0)
        hp_before = s.state.reincarnator.hp
        texts = _module_interaction_resolve(opt, s, True)
        assert any("代价" in t for t in texts)
        assert s.state.reincarnator.hp < hp_before
        assert opt["clue"] in s.state.resolved_elements

    def test_interaction_result_text(self, composer):
        from trpg_agent_web.web_server import _module_interaction_options, _module_interaction_resolve
        s = _session_with_rein()
        opts = _module_interaction_options(composer, None, "dungeon_juon_diary::diary", s)
        opt = next(iter(opts.values()))
        texts = _module_interaction_resolve(opt, s, True)
        assert any(t.startswith("📜") for t in texts)


# ── BOSS 阶段 ──────────────────────────────────────────

class TestPhaseTriggers:
    def test_phase_triggered_on_low_hp(self):
        enc = CombatEncounter.from_dict({
            "id": "boss", "title": "Boss",
            "enemies": [{"id": "b", "name": "Boss", "hp": 40}],
            "phase_thresholds": [
                {"threshold": 0.5, "name": "狂暴", "attack_bonus": 2, "behavior": "狂暴化"},
            ],
        })
        enc.start()
        enemy = enc.enemies[0]
        enemy.take_damage(25)  # 40→15，比例 0.375 < 0.5
        events = enc.check_phase_triggers()
        assert len(events) == 1
        assert events[0]["name"] == "狂暴"
        assert enemy.attack_bonus == 2  # 攻击加值提升

    def test_phase_once_only(self):
        enc = CombatEncounter.from_dict({
            "id": "boss", "title": "Boss",
            "enemies": [{"id": "b", "name": "Boss", "hp": 20}],
            "phase_thresholds": [
                {"threshold": 0.5, "name": "狂暴", "attack_bonus": 2, "behavior": "狂暴化"},
            ],
        })
        enc.start()
        enc.enemies[0].take_damage(15)  # 5/20 = 0.25 < 0.5
        assert len(enc.check_phase_triggers()) == 1
        assert len(enc.check_phase_triggers()) == 0  # 不重复触发

    def test_no_trigger_above_threshold(self):
        enc = CombatEncounter.from_dict({
            "id": "boss", "title": "Boss",
            "enemies": [{"id": "b", "name": "Boss", "hp": 20}],
            "phase_thresholds": [
                {"threshold": 0.5, "name": "狂暴", "attack_bonus": 2, "behavior": "狂暴化"},
            ],
        })
        enc.start()
        enc.enemies[0].take_damage(5)  # 15/20 = 0.75 > 0.5
        assert enc.check_phase_triggers() == []

    def test_resolver_includes_phase_events(self):
        enc = CombatEncounter.from_dict({
            "id": "boss", "title": "Boss",
            "enemies": [{"id": "b", "name": "Boss", "hp": 20, "armor": 0}],
            "phase_thresholds": [
                {"threshold": 0.5, "name": "狂暴", "attack_bonus": 2, "behavior": "狂暴化"},
            ],
        })
        enc.start()
        mech = CombatMechanics(enc, rng=random.Random(1))
        # 手动把敌人打到残血再结算一次——保证阶段在 resolver 内触发
        enc.enemies[0].hp = 5
        result = mech.resolve_option("攻击！【力量】常规难度检定")
        assert result.phase_events, "resolver 应返回阶段事件"
        assert result.phase_events[0]["name"] == "狂暴"
        assert "狂暴" in result.summary
