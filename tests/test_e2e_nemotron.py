"""端到端集成测试——用 OpenRouter nemotron 模型跑完整战斗流程。

用法: python tests/test_e2e_nemotron.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.combat import CombatLoop
from trpg_agent.combat.orchestrator import CombatOrchestrator
from trpg_agent.llm.remote_client import RemoteClient

MODULES_DIR = Path(__file__).resolve().parent.parent / "data" / "modules"
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not API_KEY:
    print("请设置 OPENROUTER_API_KEY 环境变量后重试")
    sys.exit(1)


def test_module_pool():
    """测试 1：模块池加载 + 静态校验"""
    print("\n=== 测试 1：模块池加载 + 校验 ===")
    c = ModuleComposer(MODULES_DIR)
    c.load_all()
    print(f"  加载模块: {c.module_count}")

    issues = c.validate()
    if issues:
        print(f"  ❌ 校验问题 ({len(issues)}):")
        for i in issues:
            print(f"    - {i}")
    else:
        print("  ✅ 校验通过 (0 问题)")

    # 检查战斗模块
    combat_mods = [
        m.meta.id for m in c._modules.values() if m.meta.module_type == "combat"
    ]
    print(f"  战斗模块: {len(combat_mods)} 个 ({', '.join(combat_mods[:3])}...)")
    return c


def test_combat_compile():
    """测试 2：战斗模块编译"""
    print("\n=== 测试 2：战斗模块编译 ===")
    c = ModuleComposer(MODULES_DIR)
    c.load_all()
    bundle = c.compile(seed=42, max_depth=3, start_module="deep_one_lair")
    adv = bundle.adventure

    scene = adv.get_scene(adv.start_scene)
    assert scene is not None, "起始场景不应为空"
    assert scene.combat is not None, "起始场景应有 combat 字段"
    assert scene.combat.get("enabled"), "combat.enabled 应为 True"
    encounter = scene.combat["encounter"]
    assert encounter.enemies, "应有敌人"
    print(f"  ✅ 战斗场景编译成功: {encounter.title}")
    print(f"  敌人: {'、'.join(e.name for e in encounter.enemies)}")
    print(f"  环境: {encounter.environment.terrain}")
    return adv, scene


def test_combat_mechanics_local():
    """测试 3：机制层本地测试（不调 LLM）"""
    print("\n=== 测试 3：战斗机制层 ===")
    c = ModuleComposer(MODULES_DIR)
    c.load_all()
    bundle = c.compile(seed=42, max_depth=2, start_module="deep_one_lair")
    scene = bundle.adventure.get_scene(bundle.adventure.start_scene)
    encounter = scene.combat["encounter"]

    loop = CombatLoop(encounter, investigators_state="测试员 HP 10/10 SAN 60/60")

    # 用假 LLM 输出模拟一轮
    fake_enter = """洞穴深处传来低沉的咆哮，黑色的水面泛起诡异的涟漪。

---
**正面迎战**
挥刀砍向近前的深潜者。代价：暴露在攻击范围内。检定：STR 检定（普通）。
---
**利用地形**
躲到钟乳石后伺机而动。代价：错过先手。检定：DEX 检定（简单）。
---
**投掷火把**  
点燃最后一支火把驱赶深潜者。代价：消耗火把。检定：敏捷检定（困难）。
"""
    round_state = loop.start_round(fake_enter)
    print(f"  开场叙事: {round_state.opening_narration[:60]}...")
    print(f"  选项数: {len(round_state.options)}")

    loop.submit_vote("A")
    mech_result = loop.run_mechanics()
    print(f"  检定: {mech_result.skill} 难度 {mech_result.difficulty} → "
          f"{'✅ 成功' if mech_result.success else '❌ 失败'}")
    if mech_result.test_result:
        print(f"  掷骰: {mech_result.test_result.roll} (目标 ≤{mech_result.test_result.target})")
    print(f"  敌人伤害: {mech_result.damage_to_enemies}")
    print(f"  调查员受伤: {mech_result.damage_to_investigators}")

    # 多轮直到结局
    rounds = 1
    for _ in range(10):
        if mech_result.outcome is not None:
            break
        loop.start_round(fake_enter)
        loop.submit_vote("A")
        mech_result = loop.run_mechanics()
        rounds += 1

    print(f"  战斗轮数: {rounds}")
    if mech_result.outcome:
        print(f"  结局: {mech_result.outcome.label or mech_result.outcome.id}")
    print(f"  摘要: {loop.combat_summary}")
    print("  ✅ 机制层测试完成")
    return loop


async def test_llm_combat_round():
    """测试 4：LLM 驱动的完整战斗回合"""
    print("\n=== 测试 4：LLM 战斗回合（nemotron） ===")
    client = RemoteClient(
        base_url="https://openrouter.ai/api/v1",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        api_key=API_KEY,
        timeout=180.0,
    )

    c = ModuleComposer(MODULES_DIR)
    c.load_all()
    bundle = c.compile(seed=42, max_depth=2, start_module="deep_one_lair")
    scene = bundle.adventure.get_scene(bundle.adventure.start_scene)
    encounter = scene.combat["encounter"]

    loop = CombatLoop(encounter, investigators_state="Jack HP 10/10 SAN 55/60")

    # ── 进场：LLM 生成叙事 + 选项 ──
    sys_p = loop.build_enter_prompt()
    usr_p = loop.build_enter_user_prompt()
    print("  发送 enter prompt ({:.0f} chars)...".format(len(sys_p) + len(usr_p)))
    enter_output = await client.chat(sys_p, [{"role": "user", "content": usr_p}])
    print(f"  LLM 回复: {len(enter_output)} 字符")

    round_state = loop.start_round(enter_output)
    print(f"  开场: {round_state.opening_narration[:80]}...")
    for opt in round_state.options:
        print(f"    [{opt.option_key}] {opt.label}")

    # ── 投票 + 机制结算 ──
    loop.submit_vote("A")
    mech_result = loop.run_mechanics()
    print(f"  机制: {mech_result.skill}检定 → {'✅' if mech_result.success else '❌'}")
    print(f"  伤害: 敌人 {mech_result.damage_to_enemies}, 调查员 {mech_result.damage_to_investigators}")

    # ── 叙事润色 ──
    res_sys = loop.build_resolve_prompt()
    res_usr = loop.build_resolve_user_prompt(mech_result=mech_result)
    print("  发送 resolve prompt...")
    resolution = await client.chat(res_sys, [{"role": "user", "content": res_usr}])
    print(f"  LLM 叙事: {len(resolution)} 字符")
    print(f"  叙事预览: {resolution[:120]}...")

    outcome = loop.resolve(resolution, mech_result=mech_result)
    if outcome:
        summary = loop.end_summary()
        print(f"  结局: {outcome.id} → {summary[:100]}")
    else:
        print("  战斗继续（未达结局）")

    print(f"  战斗摘要: {loop.combat_summary}")
    print("  ✅ LLM 战斗回合完成")


async def main():
    print("=" * 60)
    print("trpg-agent-web 端到端测试 — nemotron 模型")
    print("=" * 60)

    # 测试 1-3：不调 LLM
    test_module_pool()
    test_combat_compile()
    test_combat_mechanics_local()

    # 测试 4：调 LLM
    try:
        await test_llm_combat_round()
    except Exception as e:
        print(f"  ⚠️ LLM 测试失败: {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
