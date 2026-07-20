"""多人联机 + 存档系统测试。"""

from __future__ import annotations

import tempfile
import shutil
from pathlib import Path

from trpg_agent.session import Session
from trpg_agent.memory.game_state import Investigator, Npc, Quest


def test_speaker_in_record_turn():
    """speaker 参数传递到 history 条目。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("multi_test", data_dir=tmpdir)
        session.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )
        session.state.investigators.append(
            Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45)
        )

        # 陈明行动
        session.record_turn("我检查门后的暗格", "门后有一个上锁的铁盒。", speaker="陈明")
        # 林晓行动
        session.record_turn("我用发卡试着撬锁", "咔嗒一声，锁开了。", speaker="林晓")

        entries = session.history.entries()
        # 第1条 user 消息应该有 speaker
        assert entries[0]["speaker"] == "陈明"
        assert entries[0]["content"] == "我检查门后的暗格"
        # 第2条 assistant 消息应该没有 speaker
        assert "speaker" not in entries[1]
        # 第3条 user
        assert entries[2]["speaker"] == "林晓"

        print("✓ speaker 字段正确写入 history")
    finally:
        shutil.rmtree(tmpdir)


def test_build_messages_with_speaker():
    """speaker 前缀注入 Ollama 消息。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("msg_test", data_dir=tmpdir)
        msgs = session.build_messages("我推开大门", speaker="陈明")
        assert msgs[-1]["content"] == "[陈明] 我推开大门"

        # 带检定上下文
        msgs2 = session.build_messages(
            "我检查血迹", speaker="林晓", dice_context="侦查 成功 (42 ≤ 60)"
        )
        assert "[林晓]" in msgs2[-1]["content"]
        assert "侦查 成功" in msgs2[-1]["content"]

        print("✓ build_messages speaker 前缀正确")
    finally:
        shutil.rmtree(tmpdir)


def test_save_and_load():
    """命名存档 + 读档往返。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("save_test", data_dir=tmpdir)
        session.state.investigators.append(
            Investigator(name="陈明", hp=8, max_hp=12, san=50, max_san=60, luck=30)
        )
        session.state.location = "废弃医院大厅"
        session.state.quests.append(Quest(title="寻找失踪的病人", status="open"))
        session.state.npcs.append(
            Npc(name="值班护士", attitude="wary", location="废弃医院大厅")
        )

        session.record_turn("我环顾四周", "大厅空无一人，但你能听到远处有滴水声。", speaker="陈明")
        session.record_turn("我朝滴水声的方向走去", "走廊尽头是一扇半开的铁门。", speaker="陈明")

        # 存档
        save_path = session.save_game("医院探险_第2轮")
        assert save_path.is_dir()
        assert (save_path / "state.json").is_file()
        assert (save_path / "history.jsonl").is_file()

        # 列出存档
        saves = Session.list_saves("save_test")
        assert "医院探险_第2轮" in saves

        # 读档
        loaded = Session.load_game("save_test", "医院探险_第2轮", data_dir=tmpdir)
        assert loaded is not None
        assert loaded.state.turn_count == 2
        assert loaded.state.location == "废弃医院大厅"
        assert loaded.state.investigators[0].hp == 8
        assert loaded.state.investigators[0].name == "陈明"
        assert loaded.history.count() == 4  # 2 user + 2 assistant
        assert loaded.state.quests[0].title == "寻找失踪的病人"

        # 验证 speaker 在读档后保留
        entries = loaded.history.entries()
        assert entries[0]["speaker"] == "陈明"

        print("✓ 存档/读档往返正确")
    finally:
        shutil.rmtree(tmpdir)


def test_delete_save():
    """删除存档。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("del_test", data_dir=tmpdir)
        session.save_game("临时存档")
        assert "临时存档" in Session.list_saves("del_test")

        Session.delete_save("del_test", "临时存档")
        assert "临时存档" not in Session.list_saves("del_test")

        print("✓ 删除存档正确")
    finally:
        shutil.rmtree(tmpdir)


def test_load_nonexistent():
    """读不存在的存档返回 None。"""
    result = Session.load_game("no_such", "no_such_save")
    assert result is None
    print("✓ 不存在存档返回 None")


def test_backward_compat():
    """无 speaker 时完全向后兼容。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("compat_test", data_dir=tmpdir)
        # 不带 speaker 的调用（旧式）
        session.record_turn("我检查房间", "房间很暗，什么也看不清。")
        entries = session.history.entries()
        assert "speaker" not in entries[0]
        assert entries[0]["content"] == "我检查房间"

        # build_messages 不带 speaker
        msgs = session.build_messages("继续前进")
        assert "[陈明]" not in msgs[-1]["content"]
        assert msgs[-1]["content"] == "继续前进"

        print("✓ 向后兼容（无 speaker）")
    finally:
        shutil.rmtree(tmpdir)


def test_multiplayer_full_flow():
    """完整多人游戏流程测试。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("full_test", data_dir=tmpdir)
        session.state.investigators = [
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50),
            Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45),
        ]
        session.state.location = "古屋门厅"

        # 陈明探查
        session.record_turn(
            "我用手电筒扫过墙壁，寻找暗门。",
            "光束照到一幅歪斜的家族肖像。在画框的角落，你注意到一个几乎看不见的凹槽。",
            speaker="陈明"
        )
        # 林晓跟进
        session.record_turn(
            "我走近那幅画，用手指摸索凹槽。",
            "凹槽里藏着一枚生锈的铜钥匙。",
            speaker="林晓"
        )

        assert session.state.turn_count == 2
        assert session.history.count() == 4

        # 存档
        session.save_game("古屋_门前")
        # 删除当前 session，模拟重新登录
        session2 = Session.load_game("full_test", "古屋_门前", data_dir=tmpdir)
        assert session2 is not None

        # 一个人续局
        session2.record_turn(
            "我决定独自进入地下室。",
            "楼梯吱嘎作响，每走一步都像在惊动什么沉睡的东西。",
            speaker="陈明"
        )
        assert session2.state.turn_count == 3

        print("✓ 多人→存档→单人续局 完整流程")
    finally:
        shutil.rmtree(tmpdir)


# ── Phase 4.5 新增：RPG 风格存档系统 ──────────────────

def test_auto_save_at_custom_interval():
    """配置间隔后按时触发自动存档。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # 设为 3 轮便于测试
        session = Session("custom_interval_test", data_dir=tmpdir, auto_save_interval=3)
        session.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )

        for i in range(1, 4):
            session.record_turn(f"第{i}轮", f"答{i}", speaker="陈明")

        saves = Session.list_saves("custom_interval_test")
        assert "auto_save" in saves
        loaded = Session.load_game("custom_interval_test", "auto_save", data_dir=tmpdir)
        assert loaded is not None
        assert loaded.state.turn_count == 3

        print("✓ 自定义间隔自动存档")
    finally:
        shutil.rmtree(tmpdir)


def test_auto_save_disabled():
    """间隔设为 0 时禁用自动存档。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("disabled_test", data_dir=tmpdir, auto_save_interval=0)
        session.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )

        for i in range(10):
            session.record_turn(f"第{i+1}轮", f"答{i+1}", speaker="陈明")

        saves = Session.list_saves("disabled_test")
        assert "auto_save" not in saves

        print("✓ 自动存档已禁用")
    finally:
        shutil.rmtree(tmpdir)


def test_auto_save_overwrites():
    """自动存档覆盖旧槽位。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("overwrite_test", data_dir=tmpdir, auto_save_interval=3)
        session.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )

        # 第 1-3 轮 → auto_save (turn_count=3)
        for i in range(1, 4):
            session.record_turn(f"第{i}轮", f"答{i}", speaker="陈明")
        assert session.state.turn_count == 3

        # 第 4-6 轮 → auto_save 覆盖 (turn_count=6)
        for i in range(4, 7):
            session.record_turn(f"第{i}轮", f"答{i}", speaker="陈明")
        assert session.state.turn_count == 6

        loaded = Session.load_game("overwrite_test", "auto_save", data_dir=tmpdir)
        assert loaded is not None
        assert loaded.state.turn_count == 6

        print("✓ 自动存档覆盖旧槽位")
    finally:
        shutil.rmtree(tmpdir)


def test_auto_save_counter_reset_on_load():
    """加载存档后自动存档计数器归零。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("reset_test", data_dir=tmpdir, auto_save_interval=3)
        session.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )

        for i in range(1, 4):
            session.record_turn(f"第{i}轮", f"答{i}", speaker="陈明")

        loaded = Session.load_game("reset_test", "auto_save", data_dir=tmpdir)
        assert loaded is not None
        assert loaded._turns_since_save == 0

        for i in range(4, 6):
            loaded.record_turn(f"第{i}轮", f"答{i}", speaker="陈明")
        assert loaded._turns_since_save == 2

        print("✓ 加载后自动存档计数器归零")
    finally:
        shutil.rmtree(tmpdir)


def test_detect_save_intent():
    """NL 保存意图检测。"""
    # 关键词触发
    assert Session.detect_save_intent("保存当前进度") is not None
    assert Session.detect_save_intent("记录当前进度") is not None
    assert Session.detect_save_intent("存个档吧") is not None
    assert Session.detect_save_intent("帮我保存进度") is not None
    assert Session.detect_save_intent("储存进度") is not None
    assert Session.detect_save_intent("备份一下进度吧") is not None

    # 自定义名称
    result = Session.detect_save_intent("保存为 boss战前")
    assert result == "boss战前", f"期望 'boss战前'，得到 '{result}'"

    result2 = Session.detect_save_intent("存档为 医院探索完成")
    assert result2 == "医院探索完成", f"期望 '医院探索完成'，得到 '{result2}'"

    # 非保存指令
    assert Session.detect_save_intent("我推开门") is None
    assert Session.detect_save_intent("检查血迹") is None

    print("✓ NL 保存意图检测")


def test_detect_load_intent():
    """NL 加载意图检测。"""
    assert Session.detect_load_intent("加载我的进度") is True
    assert Session.detect_load_intent("读取存档") is True
    assert Session.detect_load_intent("继续游戏") is True
    assert Session.detect_load_intent("接上次的进度") is True
    assert Session.detect_load_intent("载入进度") is True

    # 非加载指令
    assert Session.detect_load_intent("我继续往前走") is False
    assert Session.detect_load_intent("推开门") is False

    print("✓ NL 加载意图检测")


def test_is_multiplayer():
    """is_multiplayer 属性。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # 单人
        s1 = Session("solo_test", data_dir=tmpdir)
        s1.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )
        assert s1.is_multiplayer is False

        # 双人
        s2 = Session("duo_test", data_dir=tmpdir)
        s2.state.investigators = [
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50),
            Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45),
        ]
        assert s2.is_multiplayer is True

        print("✓ is_multiplayer 属性")
    finally:
        shutil.rmtree(tmpdir)


def test_loaded_state_summary():
    """loaded_state_summary 人性化摘要——含物品、状态、线索、任务。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("summary_test", data_dir=tmpdir)
        inv_chen = Investigator(name="陈明", hp=8, max_hp=12, san=50, max_san=60, luck=30)
        inv_chen.inventory = ["旧钥匙", ".38左轮手枪"]
        inv_chen.conditions = ["重伤"]
        inv_lin = Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45)
        inv_lin.inventory = ["手电筒"]
        session.state.investigators = [inv_chen, inv_lin]
        session.state.location = "废弃医院大厅"
        session.state.resolved_elements = {"clue_diary", "door_unlocked"}
        session.state.quests = [
            Quest(title="找到失踪的病人", status="open"),
            Quest(title="调查护士办公室", status="resolved"),
        ]
        session.state.npcs.append(
            Npc(name="值班护士", attitude="wary", description="瘦削的中年女人", location="废弃医院大厅")
        )
        session.record_turn("环顾四周", "空无一人。", speaker="陈明")
        session.save_game("完整存档")

        loaded = Session.load_game("summary_test", "完整存档", data_dir=tmpdir)
        assert loaded is not None

        summary = loaded.loaded_state_summary()
        assert "📂 存档: 完整存档" in summary
        assert "🎭 模式: 多人" in summary
        assert "📍 地点: 废弃医院大厅" in summary
        assert "🔄 回合: 第 1 轮" in summary
        assert "🔍 已收集线索: clue_diary, door_unlocked" in summary
        assert "📋 进行中任务: 找到失踪的病人" in summary
        assert "✅ 已完成任务: 调查护士办公室" in summary
        assert "陈明 HP:8/12 SAN:50/60 LUCK:30 [重伤]" in summary
        assert "🎒 旧钥匙, .38左轮手枪" in summary
        assert "林晓 HP:10/10 SAN:70/70 LUCK:45" in summary
        assert "🎒 手电筒" in summary
        assert "🧑 在场 NPC:" in summary
        assert "值班护士 (警惕)" in summary

        print("✓ loaded_state_summary")
    finally:
        shutil.rmtree(tmpdir)


def test_loaded_from_tracking():
    """loaded_from 属性追踪存档来源。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("track_test", data_dir=tmpdir, auto_save_interval=3)
        session.state.investigators.append(
            Investigator(name="陈明", hp=12, max_hp=12, san=60, max_san=60, luck=50)
        )
        assert session.loaded_from is None

        session.save_game("手动_测试")
        assert session.loaded_from == "手动_测试"

        # 跑 3 轮触发 auto_save
        for i in range(3):
            session.record_turn(f"行动{i+1}", f"回答{i+1}", speaker="陈明")
        assert session.loaded_from == "auto_save"

        loaded = Session.load_game("track_test", "手动_测试", data_dir=tmpdir)
        assert loaded is not None
        assert loaded.loaded_from == "手动_测试"

        print("✓ loaded_from 追踪")
    finally:
        shutil.rmtree(tmpdir)


def test_multiplayer_load_all_investigators():
    """多人存档加载时所有调查员同时恢复。"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        session = Session("multi_load_test", data_dir=tmpdir)
        session.state.investigators = [
            Investigator(name="陈明", hp=8, max_hp=12, san=50, max_san=60, luck=30),
            Investigator(name="林晓", hp=10, max_hp=10, san=70, max_san=70, luck=45),
            Investigator(name="王刚", hp=15, max_hp=15, san=40, max_san=40, luck=55),
        ]
        session.state.location = "古屋地下室"
        session.record_turn("我检查门", "门锁着。", speaker="陈明")
        session.record_turn("我找钥匙", "在抽屉里。", speaker="林晓")
        session.save_game("三人冒险")

        # 加载 — 三个调查员应该全在
        loaded = Session.load_game("multi_load_test", "三人冒险", data_dir=tmpdir)
        assert loaded is not None
        assert loaded.is_multiplayer is True
        assert len(loaded.state.investigators) == 3
        names = {i.name for i in loaded.state.investigators}
        assert names == {"陈明", "林晓", "王刚"}

        # 每人可以继续行动
        loaded.record_turn("我推门进入", "门后一片漆黑。", speaker="王刚")
        assert loaded.state.turn_count == 3

        print("✓ 多人存档全量加载")
    finally:
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    test_speaker_in_record_turn()
    test_build_messages_with_speaker()
    test_save_and_load()
    test_delete_save()
    test_load_nonexistent()
    test_backward_compat()
    test_multiplayer_full_flow()
    test_auto_save_at_custom_interval()
    test_auto_save_disabled()
    test_auto_save_overwrites()
    test_auto_save_counter_reset_on_load()
    test_detect_save_intent()
    test_detect_load_intent()
    test_is_multiplayer()
    test_loaded_state_summary()
    test_loaded_from_tracking()
    test_multiplayer_load_all_investigators()
    print()
    print("全部多人联机+RPG存档测试通过 ✓")
