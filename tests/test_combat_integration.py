"""集成测试：验证 CombatLoop 接入 web_server 使用的调用序列。

用假 LLM 输出模拟战斗回合，覆盖 web_server.py 战斗分支实际使用的方法链：
build_enter_prompt → start_round → submit_vote → build_resolve_prompt → resolve →
（结局达成后）session.move_to_scene。
"""

from pathlib import Path

import pytest

from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.combat import CombatLoop
from trpg_agent.session import Session

MODULES_DIR = Path(__file__).resolve().parent.parent / "data" / "modules"

FAKE_ENTER_OUTPUT = """洞穴里传来低沉的咆哮，水面泛起涟漪。
---
**正面迎战**
挥刀砍向近前的深潜者。代价：暴露在攻击范围内。检定：STR 检定（普通）。
---
**利用地形**
躲到岩石后伺机而动。代价：错过先手。检定：DEX 检定（简单）。
---
**投掷火把**
点燃火把驱赶深潜者。代价：消耗最后一支火把。检定：敏捷检定（困难）。
"""

FAKE_RESOLVE_VICTORY = "长老在猛烈打击下轰然倒地，巢穴恢复了令人窒息的寂静。战斗胜利结束。"


def _load_combat_encounter():
    composer = ModuleComposer(MODULES_DIR)
    composer.load_all()
    bundle = composer.compile(seed=1, max_depth=2, start_module="deep_one_lair")
    adventure = bundle.adventure
    start_scene = adventure.get_scene(adventure.start_scene)
    return adventure, start_scene


def test_combat_scene_bridged_correctly():
    """module_composer 是否正确把战斗模块桥接成 combat.enabled 场景。"""
    adventure, start_scene = _load_combat_encounter()
    assert start_scene is not None
    assert start_scene.combat is not None
    assert start_scene.combat.get("enabled") is True
    encounter = start_scene.combat["encounter"]
    assert encounter is not None
    assert encounter.enemies


def test_combat_loop_round_trip_with_manual_damage():
    """完整走一遍 enter→vote→resolve，手动扣血模拟 LLM 判定胜利后，
    验证 outcome 跳转到的过渡场景 ID 与 web_server.py 里拼接的一致。
    """
    adventure, start_scene = _load_combat_encounter()
    encounter = start_scene.combat["encounter"]

    session = Session("test_combat_session", auto_save_interval=0)
    session.state.scene_id = adventure.start_scene

    loop = CombatLoop(encounter, investigators_state="测试调查员 HP 10/10 SAN 60/60")

    enter_sys = loop.build_enter_prompt()
    enter_usr = loop.build_enter_user_prompt()
    assert enter_sys
    assert enter_usr

    round_state = loop.start_round(FAKE_ENTER_OUTPUT)
    assert len(round_state.options) == 3
    assert loop.current_phase == "voting"

    chosen = loop.submit_vote("A")
    assert chosen is not None

    res_sys = loop.build_resolve_prompt()
    res_usr = loop.build_resolve_user_prompt()
    assert res_sys
    assert res_usr

    # 模拟 LLM 判定长老死亡——当前 CombatLoop 不解析叙事文本，
    # 需要外部代码显式扣血才能让 check_outcome() 识别到胜利。
    for enemy in encounter.living_enemies():
        enemy.take_damage(999)

    outcome = loop.resolve(FAKE_RESOLVE_VICTORY)
    assert outcome is not None
    assert outcome.id == "victory"

    module_id = start_scene.id.split("::", 1)[0]
    target_scene_id = f"{module_id}::combat_{outcome.id}"
    moved = session.move_to_scene(target_scene_id, adventure)
    assert moved is not None
    assert moved.id == target_scene_id


def test_combat_loop_never_ends_without_external_damage_or_force_end():
    """已知限制的回归测试：resolve() 不解析 LLM 叙事文本判定伤害/逃跑，
    如果没有外部代码扣血或调用 force_end()，纯文本"胜利"叙事不会触发结局。
    """
    _, start_scene = _load_combat_encounter()
    encounter = start_scene.combat["encounter"]

    loop = CombatLoop(encounter, investigators_state="")
    loop.start_round(FAKE_ENTER_OUTPUT)
    loop.submit_vote("A")
    outcome = loop.resolve(FAKE_RESOLVE_VICTORY)
    assert outcome is None  # enemy.hp 从未被扣减，check_outcome() 找不到结局


def test_combat_loop_force_end_safety_net():
    """force_end() 应该能在未达成结局条件时强制结束战斗（回合数超限兜底用）。"""
    _, start_scene = _load_combat_encounter()
    encounter = start_scene.combat["encounter"]

    loop = CombatLoop(encounter, investigators_state="")
    loop.start_round(FAKE_ENTER_OUTPUT)
    loop.submit_vote("A")
    loop.resolve(FAKE_RESOLVE_VICTORY)  # 未达结局，战斗继续

    outcome = loop.force_end("flee")
    assert outcome is not None
    assert outcome.id == "flee"
    assert loop.is_ended
    assert loop.outcome is outcome

    # 无效 outcome id 应返回 None，不改变状态
    loop2 = CombatLoop(encounter, investigators_state="")
    result = loop2.force_end("not_a_real_outcome")
    assert result is None


def test_combat_outcome_scenes_each_have_independent_continuation():
    """回归测试：module_composer._process_node 曾经把所有出口边统一挂到
    "模块场景列表最后一个" 场景上——对战斗模块来说这是错的，因为
    victory/defeat/flee 是三个互斥的独立结局场景，而不是同一收敛场景的
    并列分支。修复前：只有一个结局场景（恰好排在最后的那个）能继续冒险，
    另外两个 leads_to 永远是空的死胡同，且那一个场景还会错误地继承
    其它结局的 exit_labels。

    这里验证 deep_one_lair 的三个结局场景都各自拥有自己的 leads_to/exit_labels，
    互不覆盖、互不为空。
    """
    composer = ModuleComposer(MODULES_DIR)
    composer.load_all()
    bundle = composer.compile(seed=1, max_depth=2, start_module="deep_one_lair")
    adventure = bundle.adventure

    for outcome_id, expected_label in (
        ("victory", "歼灭深潜者，搜索巢穴"),
        ("defeat", "被拖入深海"),
        ("flee", "逃回洞穴入口"),
    ):
        scene = adventure.get_scene(f"deep_one_lair::combat_{outcome_id}")
        assert scene is not None, f"{outcome_id} 场景应存在"
        assert scene.leads_to, f"{outcome_id} 场景不应是死胡同"
        # 该结局场景的 exit_labels 应该只包含自己的标签，不能混入其它结局的标签
        labels = set(scene.exit_labels.values())
        assert labels == {expected_label}, (
            f"{outcome_id} 场景的 exit_labels 不应包含其它结局的标签，实际: {labels}"
        )
