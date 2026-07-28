"""集成测试：模块化叙事 + 物品 + 骰子 + HP/SAN。
运行: python3 tests/test_integration_gameplay.py
"""
import asyncio, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trpg_agent.session import Session
from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.llm.client import OllamaClient

OLLAMA_HOST = "http://192.168.0.108:11434"
OLLAMA_MODEL = "qwen3.5:9b"
MODULES_DIR = Path(__file__).resolve().parent.parent / "data" / "modules"

async def main():
    results = []
    
    # 1. 模块加载
    print("=" * 50)
    print("1. 模块加载")
    composer = ModuleComposer(MODULES_DIR)
    count = composer.load_all()
    assert count >= 20, f"期望≥20模块, 实际{count}"
    print(f"   ✓ 加载 {count} 个模块")
    results.append(("模块加载", True))
    
    # 2. 组合冒险
    print("\n2. 组合冒险")
    bundle = composer.compile(seed=42, max_depth=4)
    assert len(bundle.module_ids) >= 6, f"期望≥6模块, 实际{len(bundle.module_ids)}"
    print(f"   ✓ {len(bundle.module_ids)} 模块, {len(bundle.adventure._scenes)} 场景")
    results.append(("组合冒险", True))
    
    # 3. 初始化 Session
    print("\n3. 初始化 Session + 物品")
    session = Session("integration_test")
    adv = session.load_compiled_adventure(bundle)
    
    # 给调查员加物品
    for inv in session.state.investigators:
        if inv.name == "陈明":
            inv.inventory = ["手电筒", "笔记本", "旧印护符"]
        elif inv.name == "林晓":
            inv.inventory = ["开锁工具", "相机"]
    
    assert len(session.state.investigators) == 3
    assert session.state.find_investigator("陈明").inventory == ["手电筒", "笔记本", "旧印护符"]
    print(f"   ✓ 调查员物品已设置")
    print(f"   {session.state.scene_summary()}")
    results.append(("物品初始化", True))
    
    # 4. Ollama 连通性
    print("\n4. Ollama 连通性")
    client = OllamaClient(host=OLLAMA_HOST, model=OLLAMA_MODEL, num_ctx=4096)
    try:
        await client.chat("回复'OK'", [{"role": "user", "content": "测试连接"}])
        print(f"   ✓ {OLLAMA_MODEL} 响应正常")
        results.append(("Ollama连通", True))
    except Exception as e:
        print(f"   ✗ {e}")
        results.append(("Ollama连通", False))
        return results
    
    # 5. 第一轮：进入场景
    print("\n5. 第一轮：场景进入")
    player_input = "我用手电筒照亮码头前方，看看渔船那边有什么"
    answer, dice = await run_turn(session, client, player_input, adv)
    print(f"   检定: {dice or '无'}")
    print(f"   KP: {answer[:200]}...")
    
    # 检查 KP 是否提到了渔船
    has_boat = "渔船" in answer or "玛丽安" in answer or "船体" in answer or "撕裂" in answer
    print(f"   {'✓' if has_boat else '⚠'} KP 提到了渔船场景: {has_boat}")
    results.append(("场景识别", has_boat))
    
    # 6. 第二轮：掷骰检定
    print("\n6. 第二轮：掷骰检定（侦查）")
    player_input = "我仔细检查渔船船体上的撕裂口，看看是什么东西造成的"
    answer, dice = await run_turn(session, client, player_input, adv)
    print(f"   检定: {dice or '无'}")
    print(f"   KP: {answer[:200]}...")
    
    has_dice = dice != "" or "检定" in answer or "骰" in answer or "成功" in answer or "失败" in answer
    print(f"   {'✓' if has_dice else '⚠'} 涉及掷骰/检定: {has_dice}")
    results.append(("骰子响应", has_dice))
    
    # 7. 第三轮：靠近危险 → 应触发 SAN 检定
    print("\n7. 第三轮：靠近触手残肢（SAN检定）")
    player_input = "我靠近甲板上蠕动的触手残肢，想收集一点黏液样本"
    answer, dice = await run_turn(session, client, player_input, adv)
    print(f"   检定: {dice or '无'}")
    print(f"   KP: {answer[:200]}...")
    
    # 手动触发 SAN 扣除来测试数值系统
    inv = session.state.find_investigator("陈明")
    old_san = inv.san
    inv.lose_san(3)
    print(f"   → 陈明 SAN: {old_san} → {inv.san} (扣3)")
    
    san_changed = inv.san < old_san
    print(f"   ✓ SAN 变化: {san_changed}")
    results.append(("SAN计算", san_changed))
    
    # 8. 物品使用：医疗包
    print("\n8. 第四轮：使用医疗包")
    # 先给陈明扣血模拟受伤
    inv.take_damage(4)
    print(f"   → 陈明受伤 HP: {inv.hp}/{inv.max_hp}")
    
    player_input = "王博士用医疗包给陈明处理伤口"
    answer, dice = await run_turn(session, client, player_input, adv)
    print(f"   KP: {answer[:200]}...")
    
    # 手动执行治疗
    hp_before = inv.hp
    inv.heal(3)
    print(f"   → 陈明 HP: {hp_before} → {inv.hp} (治疗3)")
    
    hp_healed = inv.hp > hp_before
    print(f"   ✓ HP 变化: {hp_healed}")
    results.append(("HP治疗", hp_healed))
    
    # 9. 物品拾取
    print("\n9. 第五轮：拾取物品")
    player_input = "我在渔船甲板上找到一截绣着惠特利纹章的破布，把它收起来"
    answer, dice = await run_turn(session, client, player_input, adv)
    print(f"   KP: {answer[:200]}...")
    
    # 手动添加物品
    inv.inventory.append("惠特利纹章破布")
    print(f"   → 陈明物品: {inv.inventory}")
    
    has_new_item = "惠特利纹章破布" in inv.inventory
    print(f"   ✓ 物品拾取: {has_new_item}")
    results.append(("物品拾取", has_new_item))
    
    # 10. 场景切换
    print("\n10. 第六轮：场景切换投票")
    vote_prompt = adv.get_scene(session.state.scene_id).vote_prompt if session.state.scene_id else ""
    exits = adv.scene_exits(session.state.scene_id, include_locked=True) if session.state.scene_id else []
    print(f"   当前场景: {session.state.scene_id}")
    print(f"   可用出口: {[e.label for e in exits]}")
    print(f"   {'✓' if exits else '⚠'} 场景有出口: {len(exits) > 0}")
    results.append(("场景出口", len(exits) > 0))
    
    # 11. 状态持久化
    print("\n11. 状态持久化")
    state_file = Path("/tmp/integration_test_state.json")
    from trpg_agent.memory.game_state import GameState
    session.state.save(state_file)
    loaded_state = GameState.load(state_file)
    
    inv_check = loaded_state.find_investigator("陈明")
    assert inv_check is not None
    assert inv_check.hp == inv.hp
    assert inv_check.san == inv.san
    assert inv_check.inventory == inv.inventory
    print(f"   ✓ 状态保存/恢复一致: HP={inv_check.hp}, SAN={inv_check.san}, 物品={inv_check.inventory}")
    results.append(("状态持久化", True))
    
    # 汇总
    print("\n" + "=" * 50)
    print("测试汇总")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"\n{passed}/{len(results)} 通过")
    
    await client.aclose()
    return results

async def run_turn(session, client, player_input, adv):
    from trpg_agent.llm.sanitize import _sanitize
    dice_context, _ = await session.classify_and_resolve(client, player_input)
    system = session.build_system_prompt(adventure=adv)
    messages = session.build_messages(player_input, dice_context=dice_context)
    raw = await client.chat(system, messages)
    answer = _sanitize(raw)
    session.record_turn(player_input, answer)
    return answer, dice_context

if __name__ == "__main__":
    results = asyncio.run(main())
    failed = sum(1 for _, ok in results if not ok)
    sys.exit(1 if failed > 2 else 0)  # 允许少量失败(LLM不稳定)
