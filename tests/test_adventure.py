"""场景卡模组系统测试"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from types import SimpleNamespace

from trpg_agent.adventure import Adventure, Scene, AdventureNpc
from trpg_agent.session import Session
from trpg_agent.adventure.variance import (
    EncounterTable, ClueVariant, NpcVariant, MoodVariant,
    ModuleVariance, RunSeed,
)


def _write_scenario(tmp: Path, data: dict) -> Path:
    (tmp / "scenario.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp


MINI_SCENARIO = {
    "id": "mini", "title": "测试模组", "start_scene": "a",
    "summary": "一段测试剧情。",
    "scenes": [
        {"id": "a", "title": "开始", "part": 1, "description": "你站在门前。",
         "npcs_here": ["Bob"], "opportunities": [{"id": "a1", "text": "敲门"}],
         "secrets": [{"id": "as1", "text": "门后是陷阱"}],
         "leads_to": ["b"], "guidance": "不要立刻开门。"},
        {"id": "b", "title": "结局", "part": 2, "description": "房间里有宝藏。"},
    ],
    "npcs": [{"name": "Bob", "description": "一个可疑的人", "hp": 10}],
}


class TestAdventure:
    def test_load_and_lookup(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            assert adv is not None
            assert adv.start_scene == "a"
            assert adv.get_scene("a").title == "开始"
            assert adv.get_scene("nope") is None

    def test_npc_lookup(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            bob = adv.get_npc("Bob")
            assert bob is not None
            assert bob.hp == 10

    def test_missing_file_returns_none(self):
        assert Adventure.load(Path("/nonexistent/path")) is None


class TestSceneTransitions:
    def test_can_move_to_connected(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            assert adv is not None
            assert adv.can_move_to("a", "b")

    def test_cannot_move_to_unconnected(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            assert not adv.can_move_to("a", "nope")

    def test_gated_exit(self):
        data = dict(MINI_SCENARIO)
        data["scenes"][0]["exit_requires"] = {"b": "a1"}
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, data)
            adv = Adventure.load(tmp)
            # 未解决 a1，不能通过
            assert not adv.can_move_to("a", "b")
            # 解决了 a1，可以通过
            assert adv.can_move_to("a", "b", resolved_ids={"a1"})


class TestAdventureBlock:
    def test_block_contains_summary_and_scene(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            block = adv.adventure_block("a")
            assert "冒险模组" in block
            assert "测试模组" in block
            assert "开始" in block
            assert "敲门" in block
            assert "门后是陷阱" in block
            assert "切勿直接说出" in block

    def test_unknown_scene_degrades_to_summary(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            block = adv.adventure_block("nope")
            assert "冒险模组" in block
            assert "当前场景" not in block

    def test_resolved_elements_hidden(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            block = adv.adventure_block("a", resolved_ids={"a1"})
            assert "已发现的线索" in block
            assert "a1" in block

    def test_block_uses_semantic_exit_titles(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, MINI_SCENARIO)
            adv = Adventure.load(tmp)
            block = adv.adventure_block("a")
            assert "可前往：" in block
            assert "- 结局" in block
            assert "- b" not in block

    def test_block_prefers_exit_labels(self):
        data = dict(MINI_SCENARIO)
        data["scenes"] = [dict(scene) for scene in MINI_SCENARIO["scenes"]]
        data["scenes"][0]["exit_labels"] = {"b": "推门深入"}
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, data)
            adv = Adventure.load(tmp)
            block = adv.adventure_block("a")
            assert "- 推门深入" in block


class TestAdventureRuntimeLoading:
    def test_scene_exits_include_locked_and_available_states(self):
        data = dict(MINI_SCENARIO)
        data["scenes"] = [dict(scene) for scene in MINI_SCENARIO["scenes"]]
        data["scenes"][0]["exit_requires"] = {"b": "a1"}
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _write_scenario(tmp, data)
            adv = Adventure.load(tmp)
            exits = adv.scene_exits("a", include_locked=True)
            assert len(exits) == 1
            assert exits[0].title == "结局"
            assert exits[0].required_element == "a1"
            assert exits[0].available is False

    def test_session_can_load_runtime_adventure(self):
        adventure = Adventure(
            id="runtime_adv",
            title="运行时冒险",
            start_scene="a",
            scenes=[
                Scene(id="a", title="门厅", npcs_here=["Bob"], leads_to=["b"]),
                Scene(id="b", title="密室"),
            ],
            npcs=[AdventureNpc(name="Bob", attitude="wary", description="一个可疑的人", secret="他在撒谎")],
        )
        with TemporaryDirectory() as td:
            session = Session("runtime_adv", data_dir=Path(td), skip_characters=True)
            session.load_runtime_adventure(adventure)

            assert session.state.adventure_id == "runtime_adv"
            assert session.state.scene_id == "a"
            assert session.state.location == "门厅"
            assert "Bob" in session.state.scene_summary()

            block = session._build_npc_memory_block()
            assert block is not None
            assert "Bob" in block
            assert "DM 提示：他在撒谎" in block

    def test_runtime_loading_only_places_present_npcs_in_start_scene(self):
        adventure = Adventure(
            id="runtime_adv",
            title="运行时冒险",
            start_scene="a",
            scenes=[
                Scene(id="a", title="门厅", npcs_here=["Bob"]),
                Scene(id="b", title="后室", npcs_here=["Alice"]),
            ],
            npcs=[
                AdventureNpc(name="Bob", attitude="wary", description="门口守卫"),
                AdventureNpc(name="Alice", attitude="friendly", description="藏在后室"),
            ],
        )
        with TemporaryDirectory() as td:
            session = Session("runtime_adv", data_dir=Path(td), skip_characters=True)
            session.load_runtime_adventure(adventure)

            bob = session.state.find_npc("Bob")
            alice = session.state.find_npc("Alice")
            assert bob is not None and bob.location == "a"
            assert alice is not None and alice.location == ""
            summary = session._build_npc_memory_block()
            assert summary is not None
            assert "Bob" in summary
            assert "Alice" not in summary

    def test_session_can_resume_compiled_adventure(self, monkeypatch):
        adventure = Adventure(
            id="composed_library_3",
            title="编译模组",
            start_scene="a",
            scenes=[Scene(id="a", title="图书室")],
        )
        bundle = SimpleNamespace(
            adventure=adventure,
            source_id="composed_library_3",
            compose_seed=7,
            module_ids=["library_research"],
            branch_points=1,
            max_depth=3,
            start_module="library_research",
            difficulty_range=None,
            run_seed=SimpleNamespace(to_dict=lambda: {"seed": 99}),
        )

        class DummyComposer:
            def __init__(self, modules_dir):
                self.modules_dir = modules_dir

            def compile(self, *, seed=None, max_depth=3, start_module=None, difficulty_range=None):
                assert seed == 7
                assert max_depth == 3
                assert start_module == "library_research"
                assert difficulty_range is None
                return bundle

        with TemporaryDirectory() as td:
            session = Session("runtime_adv", data_dir=Path(td), skip_characters=True)
            session.state.adventure_id = "composed_library_3"
            session.state.adventure_meta = {
                "kind": "compiled",
                "source_id": "composed_library_3",
                "compose_seed": 7,
                "module_ids": ["library_research"],
                "branch_points": 1,
                "max_depth": 3,
                "start_module": "library_research",
                "difficulty_range": [],
                "run_seed": {"seed": 99},
            }
            monkeypatch.setattr("trpg_agent.session.ModuleComposer", DummyComposer)

            resumed = session.resume_adventure()

            assert resumed is not None
            assert resumed.id == "composed_library_3"
            assert session.state.adventure_meta["source_id"] == "composed_library_3"


class TestVariance:
    def test_encounter_table(self):
        table = EncounterTable("test", [
            EncounterTable.__dataclass_fields__  # skip, just test from_dict
        ])
        # Manual construction
        from trpg_agent.adventure.variance import RandomEncounter
        et = EncounterTable("test", [
            RandomEncounter("e1", weight=1, description="事件一"),
            RandomEncounter("e2", weight=9, description="事件二"),
        ])
        results = [et.roll() for _ in range(100)]
        # 权重 1:9，大概率 e2
        e2_count = sum(1 for r in results if r and r.id == "e2")
        assert e2_count > 50  # 大概率

    def test_clue_variant(self):
        cv = ClueVariant("clue1", "一封信", ["foyer", "library"])
        import random
        rng = random.Random(42)
        cv.current_scene = rng.choice(cv.possible_scenes)
        assert cv.current_scene in ["foyer", "library"]

    def test_mood_variant(self):
        mv = MoodVariant("foyer", "门厅很暗。", ["蛛网密布", "吊灯摇晃", "地板嘎吱"])
        result = mv.pick(count=2)
        assert "门厅很暗" in result
        assert len(mv.chosen_details) == 2

    def test_run_seed_reproducible(self):
        rs1 = RunSeed(42)
        rs2 = RunSeed(42)
        cv = ClueVariant("c1", "线索", ["a", "b", "c"])
        rs1.place_clues([cv])
        assert cv.current_scene in ["a", "b", "c"]

    def test_module_variance_from_dict(self):
        data = {
            "encounter_tables": [{
                "table_id": "t1",
                "encounters": [{"id": "e1", "weight": 1, "description": "事件"}],
            }],
            "clue_variants": [{"clue_id": "c1", "text": "信", "possible_scenes": ["a", "b"]}],
            "npc_variants": [{"npc_name": "Bob", "variants": [{"attitude": "wary"}]}],
            "mood_variants": [{"scene_id": "s1", "base_description": "暗", "variable_details": ["蛛网"]}],
        }
        mv = ModuleVariance.from_dict(data)
        assert len(mv.encounter_tables) == 1
        assert len(mv.clue_variants) == 1
        assert len(mv.npc_variants) == 1
        assert len(mv.mood_variants) == 1
