"""Phase 3 集成测试 — 检定路由 + 多轮记忆。

用法: python -m tests.test_session
前提: Ollama 运行中；可通过 OLLAMA_HOST / OLLAMA_MODEL 覆盖默认配置。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.llm.client import OllamaClient
from trpg_agent.llm.sanitize import _sanitize
from trpg_agent.session import Session

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


async def run_turn(
    client: OllamaClient,
    session: Session,
    player_input: str,
) -> str:
    """执行一轮完整的 KP 回合：分类→检定→生成→记录。"""
    # Phase 3: 自动分类 + 检定
    dice_ctx, request = await session.classify_and_resolve(client, player_input)
    if request:
        print(f"  🎲 检定: {request.skill} {request.difficulty}难度"
              f"{' (' + request.character + ')' if request.character else ''}")

    # 生成 KP 回答
    system = session.build_system_prompt()
    messages = session.build_messages(player_input, dice_context=dice_ctx)
    raw = await client.chat(system, messages)
    answer = _sanitize(raw)
    session.record_turn(player_input, answer)
    return answer


async def main():
    print(f"连接 Ollama: {OLLAMA_HOST}")
    client = OllamaClient(host=OLLAMA_HOST, model=MODEL, num_ctx=4096)

    session = Session("phase3_test")
    session.load_characters()
    session.state.location = "阿卡姆市立图书馆，深夜"
    print(session.summary())
    print(f"可用技能: {', '.join(session._collect_skills()[:10])}...")

    # ═══ 第 1 轮：明显需要侦察检定 ═══
    print("\n" + "=" * 60)
    print("[第1轮] 侦察检定")
    action = "林晓蹲下来，用手电筒仔细检查地板上的拖痕，想知道这些痕迹到底是什么造成的。"
    print(f"玩家: {action}")
    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 2 轮：不需要检定（纯对话） ═══
    print("\n" + "=" * 60)
    print("[第2轮] 纯扮演——不应触发检定")
    action = "陈明转向王博士，压低声音问：'你听说过一本叫《死灵书》的古籍吗？'"
    print(f"玩家: {action}")
    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 3 轮：神秘学知识 ═══
    print("\n" + "=" * 60)
    print("[第3轮] 知识检定")
    action = "王博士翻开那本封皮异常的古书，试图辨认里面的文字和符号属于什么时代和文明。"
    print(f"玩家: {action}")
    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 4 轮：潜行 ═══
    print("\n" + "=" * 60)
    print("[第4轮] 潜行检定")
    action = "听到走廊尽头传来脚步声，陈明迅速闪到书架后面，屏住呼吸，试图不被来人发现。"
    print(f"玩家: {action}")
    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 5 轮 ═══
    print("\n" + "=" * 60)
    print("[第5轮]")
    action = "林晓举起相机，对着那个黑影消失的方向连续拍了三张照片。"
    print(f"玩家: {action}")
    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    session.persist()
    print("\n" + "=" * 60)
    print(session.summary())

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
