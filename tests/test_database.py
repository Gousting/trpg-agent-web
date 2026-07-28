"""SQLite 数据库持久层测试 — 调查员跨 session 复用 + 声纹绑定 + 快照存档。"""

from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace

from trpg_agent.memory.database import Database
from trpg_agent.memory.game_state import Investigator, Npc, Quest, GameState
from trpg_agent.session import Session
from trpg_agent.adventure import Adventure, Scene


def test_database_crud():
    """调查员增删改查。"""
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "test.db")
    try:
        # 保存
        inv = Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50,
                           skills={"侦查": 60, "图书馆": 50})
        db.save_investigator(inv, voice_id="speaker_001")

        # 加载
        loaded = db.load_investigator("陈明")
        assert loaded is not None
        assert loaded.hp == 12
        assert loaded.skills["侦查"] == 60

        # 列表
        assert "陈明" in db.list_investigators()

        # 声纹查找
        found = db.find_investigator_by_voice("speaker_001")
        assert found is not None
        assert found.name == "陈明"

        # 更新
        inv.hp = 8
        inv.skills["侦查"] = 65
        db.save_investigator(inv)
        loaded2 = db.load_investigator("陈明")
        assert loaded2.hp == 8
        assert loaded2.skills["侦查"] == 65

        # 声纹绑定
        assert db.bind_voice("陈明", "new_speaker_99")
        found2 = db.find_investigator_by_voice("new_speaker_99")
        assert found2 is not None

        # 删除
        db.delete_investigator("陈明")
        assert db.load_investigator("陈明") is None
        assert "陈明" not in db.list_investigators()

        print("✓ 调查员 CRUD + 声纹绑定")
    finally:
        db.close()
        shutil.rmtree(tmp)


def test_session_state_roundtrip():
    """Session 状态往返：db 写入 → db 读取。"""
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "test.db")
    try:
        state = GameState(session_id="roundtrip_test")
        state.location = "古屋地下室"
        state.scene_id = "basement"
        state.adventure_meta = {"kind": "compiled", "compose_seed": 7}
        state.turn_count = 5
        state.resolved_elements = {"clue_diary", "door_unlocked"}

        inv = Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45)
        state.investigators.append(inv)
        state.npcs.append(Npc(name="管家", attitude="hostile", location="basement"))
        state.quests.append(Quest(title="找到失踪的日记", status="open"))

        # 写入
        db.create_session("roundtrip_test")
        db.save_investigator(inv)
        db.add_investigator_to_session("roundtrip_test", "林晓")
        db.save_session_state(state)
        db.save_session_npcs("roundtrip_test", state.npcs)
        db.save_session_quests("roundtrip_test", state.quests)

        # 读取
        loaded = db.load_session_state("roundtrip_test")
        assert loaded is not None
        assert loaded.location == "古屋地下室"
        assert loaded.adventure_meta["kind"] == "compiled"
        assert loaded.turn_count == 5
        assert "clue_diary" in loaded.resolved_elements
        assert loaded.investigators[0].name == "林晓"
        assert loaded.investigators[0].hp == 10
        assert loaded.npcs[0].name == "管家"
        assert loaded.quests[0].title == "找到失踪的日记"

        print("✓ Session 状态往返")
    finally:
        db.close()
        shutil.rmtree(tmp)


def test_session_with_db():
    """Session 使用 db 后端完整流程。"""
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "test.db")
    try:
        # 先注册调查员
        inv = Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50,
                           skills={"侦查": 60})
        db.save_investigator(inv)
        db.create_session("db_session_test")
        db.add_investigator_to_session("db_session_test", "陈明")

        # 创建 session with db
        session = Session("db_session_test", data_dir=tmp, db=db)

        assert session.state.investigators[0].name == "陈明"

        # 游戏回合
        session.record_turn("我检查门后的暗格", "门后有一个上锁的铁盒。", speaker="陈明")
        assert session.state.turn_count == 1
        assert session.history.count() == 2

        # 验证 db 中也有
        history = db.load_history("db_session_test")
        assert len(history) == 2
        assert history[0]["speaker"] == "陈明"

        # 存档 + 读档
        session.save_game("古屋_测试")
        assert "古屋_测试" in Session.list_saves("db_session_test", db=db)

        loaded = Session.load_game("db_session_test", "古屋_测试", data_dir=tmp, db=db)
        assert loaded is not None
        assert loaded.state.turn_count == 1
        assert loaded.history.count() == 2

        # 续局
        loaded.record_turn("我继续前进", "走廊尽头传来脚步声。", speaker="陈明")
        assert loaded.state.turn_count == 2

        print("✓ Session + db 完整流程")
    finally:
        db.close()
        shutil.rmtree(tmp)


def test_load_game_restores_runtime_adventure_context_from_db(monkeypatch):
    """命名读档后应恢复 runtime adventure，上层无需再次手动传入 adventure。"""
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "test.db")
    try:
        session = Session("resume_db_test", data_dir=tmp, db=db, skip_characters=True)
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
        session.state.location = "图书室"
        session.state.scene_id = "library_research::library"
        session.save_game("恢复测试")

        class DummyComposer:
            def __init__(self, modules_dir):
                self.modules_dir = modules_dir

            def compile(self, *, seed=None, max_depth=3, start_module=None, difficulty_range=None):
                assert seed == 7
                return SimpleNamespace(
                    adventure=Adventure(
                        id="composed_library_3",
                        title="编译模组",
                        start_scene="library_research::library",
                        summary="编译摘要",
                        scenes=[Scene(id="library_research::library", title="图书室", description="阴暗的图书室")],
                    ),
                    source_id="composed_library_3",
                    compose_seed=7,
                    module_ids=["library_research"],
                    branch_points=1,
                    max_depth=3,
                    start_module="library_research",
                    difficulty_range=None,
                    run_seed=SimpleNamespace(to_dict=lambda: {"seed": 99}),
                )

        monkeypatch.setattr("trpg_agent.session.ModuleComposer", DummyComposer)

        loaded = Session.load_game("resume_db_test", "恢复测试", data_dir=tmp, db=db)

        assert loaded is not None
        assert loaded._runtime_adventure is not None
        prompt = loaded.build_system_prompt()
        assert "冒险模组" in prompt
        assert "图书室" in prompt
    finally:
        db.close()
        shutil.rmtree(tmp)


def test_cross_session_investigator():
    """调查员跨 session 复用：一个调查员加入两个不同 session。"""
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "test.db")
    try:
        # 注册调查员
        inv = Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        db.save_investigator(inv)

        # Session A
        db.create_session("game_a")
        db.add_investigator_to_session("game_a", "陈明")
        s_a = Session("game_a", data_dir=tmp, db=db)
        s_a.state.location = "废弃医院"
        s_a.state.investigators[0].hp = 8  # 扣血
        db.save_investigator(s_a.state.investigators[0])
        db.save_session_state(s_a.state)

        # Session B（同一个人，不同的冒险）
        db.create_session("game_b")
        db.add_investigator_to_session("game_b", "陈明")
        s_b = Session("game_b", data_dir=tmp, db=db)

        # Session B 中调查员应该继承 Session A 扣过的血
        assert s_b.state.investigators[0].hp == 8
        assert s_b.state.investigators[0].name == "陈明"

        # 从 session B 移除调查员（人离开了）
        db.remove_investigator_from_session("game_b", "陈明")
        s_b2 = Session("game_b", data_dir=tmp, db=db)
        assert len(s_b2.state.investigators) == 0
        # 但调查员本身还在数据库里
        assert db.load_investigator("陈明") is not None

        print("✓ 调查员跨 session 复用")
    finally:
        db.close()
        shutil.rmtree(tmp)


def test_backward_compat_no_db():
    """无 db 时完全向后兼容。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        session = Session("compat_db_test", data_dir=tmp)
        session.state.investigators.append(
            Investigator(name="测试员", hp=10, max_hp=10, san=50, max_san=50, luck=60)
        )
        session.record_turn("测试输入", "测试回答", speaker="测试员")
        session.persist()
        assert session.state.turn_count == 1
        assert session.history.count() == 2

        # 应该写入 JSON 文件
        assert (tmp / "compat_db_test" / "state.json").is_file()
        assert (tmp / "compat_db_test" / "history.jsonl").is_file()

        print("✓ 无 db 向后兼容")
    finally:
        shutil.rmtree(tmp)


def test_migrate_from_json():
    """从旧 JSON 存档迁移到数据库。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        # 先创建 JSON 存档
        session = Session("migrate_test", data_dir=tmp)
        session.state.investigators.append(
            Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45)
        )
        session.state.location = "古屋"
        session.record_turn("我推开门", "门吱嘎作响。", speaker="林晓")
        session.save_game("旧存档")

        # 迁移
        db = Database(tmp / "test.db")
        assert db.migrate_json_save("migrate_test", "旧存档")

        # 验证迁移结果
        loaded = Session.load_game("migrate_test", "旧存档", data_dir=tmp, db=db)
        assert loaded is not None
        assert loaded.state.location == "古屋"
        assert loaded.state.investigators[0].name == "林晓"
        assert loaded.history.count() == 2

        db.close()
        print("✓ JSON → DB 迁移")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_database_crud()
    test_session_state_roundtrip()
    test_session_with_db()
    test_cross_session_investigator()
    test_backward_compat_no_db()
    test_migrate_from_json()
    print()
    print("全部数据库集成测试通过 ✓")
