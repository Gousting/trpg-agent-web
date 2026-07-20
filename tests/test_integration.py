"""Phase 4 全链路集成测试 — 模拟完整 COC 跑团 session。

用法: uv run python tests/test_integration.py
前提: Ollama 运行中，gemma4:12b 已加载
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.llm.sanitize import _sanitize
from trpg_agent.rules.sanity import SanLoss
from trpg_agent.rules.combat import ActionType

OLLAMA_HOST = "http://192.168.0.108:11434"
MODEL = "gemma4:12b"


def sep(title: str = "") -> None:
    if title:
        print(f"\n{'═' * 60}\n  {title}\n{'═' * 60}")
    else:
        print("─" * 60)


async def run_turn(
    client: OllamaClient,
    session: Session,
    player_input: str,
    *,
    san_check: tuple[str, str] | None = None,
) -> str:
    """执行一轮：分类 → 检定 → KP 生成 → 记录。"""
    # 1. 掷骰路由
    dice_ctx, roll_req = await session.classify_and_resolve(client, player_input)

    # 2. SAN 检定（如指定）
    if san_check:
        name, level = san_check
        san_result = session.perform_san_check(name, level)
        if san_result:
            extra = f"\n[SAN检定] {san_result.description}"
            dice_ctx = dice_ctx + extra if dice_ctx else extra

    # 3. 构建 prompt
    system = session.build_system_prompt()
    messages = session.build_messages(player_input, dice_context=dice_ctx)

    # 4. KP 生成
    raw = await client.chat(system, messages)
    answer = _sanitize(raw)

    # 5. 记录
    session.record_turn(player_input, answer)

    # 6. 检查压缩
    await session.maybe_compress(client)

    return answer


async def main():
    print("TRPG Agent — Phase 4 全链路集成测试")
    sep()

    client = OllamaClient(host=OLLAMA_HOST, model=MODEL, num_ctx=4096)
    session = Session("integration_test")
    sep(f"Session: {session.summary()}")

    # ── 第 1 轮：探索场景 ──
    sep("第 1 轮 — 场景探索")
    a1 = "陈明推开古屋沉重的橡木门，手电筒的光扫过布满蛛网的门厅。空气中弥漫着霉味和某种说不出的腥甜。他低声对同伴说：'小心，这地方不对劲。'"
    print(f"玩家: {a1[:60]}...")
    r1 = await run_turn(client, session, a1)
    print(f"KP: {r1[:120]}...")

    # ── 第 2 轮：侦查检定 ──
    sep("第 2 轮 — 侦查检定")
    a2 = "林晓蹲下来，用手指抹了一下地板上的暗色污渍，凑到鼻尖闻了闻：'这不是油漆……是血，而且很新鲜。'她顺着血迹看向通往地下室的楼梯。"
    print(f"玩家: {a2[:60]}...")
    r2 = await run_turn(client, session, a2)
    print(f"KP: {r2[:120]}...")

    # ── 第 3 轮：知识检定 ──
    sep("第 3 轮 — 知识检定")
    a3 = "王博士推了推眼镜，凝视着墙上褪色的家族肖像：'等等……我认识这张脸。这个家族在 1920 年代因为一起失踪案上过报纸——整个家族一夜之间人间蒸发。'他翻开随身携带的旧剪报本。"
    print(f"玩家: {a3[:60]}...")
    r3 = await run_turn(client, session, a3)
    print(f"KP: {r3[:120]}...")

    # ── 第 4 轮：遭遇恐怖 → SAN 检定 ──
    sep("第 4 轮 — 遭遇恐怖 + SAN 检定")
    a4 = "陈明推开地下室的门，手电筒光柱扫过一个巨大的、由骨头和腐烂肉体堆砌的祭坛。祭坛中央的暗色液体正在自行沸腾，某种不应该存在于这个世界的东西正在从液体中成型。"
    print(f"玩家: {a4[:60]}...")
    r4 = await run_turn(client, session, a4, san_check=("陈明", "MAJOR"))
    print(f"KP: {r4[:120]}...")

    # ── 第 5 轮：战斗 ──
    sep("第 5 轮 — 战斗")
    a5 = "'它出来了！'陈明从腰间抽出左轮手枪，瞄准那团蠕动的黑影。"
    print(f"玩家: {a5[:60]}...")
    # 手动战斗检定
    atk = session.perform_attack("陈明", "深渊之子", "FIREARMS", 60, damage_dice="1d10")
    combat_ctx = f"[战斗] {atk.description}" if atk else ""
    r5 = await run_turn(client, session, a5)
    # 注入战斗结果
    system = session.build_system_prompt()
    if combat_ctx:
        messages = session.build_messages(a5, dice_context=combat_ctx)
        raw = await client.chat(system, messages)
        r5 = _sanitize(raw)
        session.record_turn(a5, r5)
    print(f"战斗: {combat_ctx}")
    print(f"KP: {r5[:120]}...")

    # ── 第 6 轮：幸运调整 ──
    sep("第 6 轮 — 幸运与孤注一掷")
    # 模拟一次失败检定后使用幸运
    luck_desc = session.spend_luck_for_roll("陈明", 55, 50)
    print(f"幸运: {luck_desc}")

    # 模拟孤注一掷
    push = session.try_push_roll(40, "常规", previous_roll=65)
    if push:
        print(f"孤注一掷: {push['description']}")

    # ── 终局汇总 ──
    sep("终局状态")
    print(session.summary())
    print(f"\n前情提要: {session.state.recap[:200] if session.state.recap else '(空)'}")
    print(f"\n调查员状态:")
    for inv in session.state.investigators:
        print(f"  {inv.name}: HP {inv.hp}/{inv.max_hp}, SAN {inv.san}/{inv.max_san}, "
              f"Luck {inv.luck}, 状态: {inv.conditions or '正常'}")

    sep()
    print("Phase 4 全链路集成测试完成 ✓")

    session.persist()
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
