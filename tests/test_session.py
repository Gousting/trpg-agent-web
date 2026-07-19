"""Phase 2 集成测试 — Session 管理器 + 多轮记忆。

用法: uv run python tests/test_session.py
前提: Ollama 运行中，已 pull gemma4:12b
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.llm.client import OllamaClient
from trpg_agent.llm.sanitize import _sanitize
from trpg_agent.rules.coc import resolve_coc, describe_result
from trpg_agent.session import Session

OLLAMA_HOST = "http://192.168.0.108:11434"
MODEL = "gemma4:12b"


async def run_turn(
    client: OllamaClient,
    session: Session,
    player_input: str,
    *,
    dice_context: str = "",
) -> str:
    """执行一轮 KP 回合，自动记录历史。"""
    system = session.build_system_prompt()
    messages = session.build_messages(player_input, dice_context=dice_context)
    raw = await client.chat(system, messages)
    answer = _sanitize(raw)
    session.record_turn(player_input, answer)
    return answer


async def main():
    print(f"连接 Ollama: {OLLAMA_HOST}")
    client = OllamaClient(host=OLLAMA_HOST, model=MODEL, num_ctx=4096)

    # 创建 session
    session = Session("default")
    session.load_characters()
    session.state.location = "阿卡姆市立图书馆，深夜"
    print(session.summary())

    # ═══ 第 1 轮 ═══
    print("\n" + "=" * 60)
    print("[第1轮]")
    action = "陈明推开图书馆的旧木门，手电筒的光扫过积灰的书架。他回头对同伴们说：'分头找，注意任何跟神秘学有关的东西。'"
    print(f"玩家: {action}")

    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 2 轮：带检定 ═══
    print("\n" + "=" * 60)
    print("[第2轮]")
    action = "林晓蹲下来检查地板上的拖痕：'这些痕迹很新，而且一直延伸到那面墙——'她指向阅览室最里面的书架。"
    print(f"玩家: {action}")

    result = resolve_coc(55, "常规")  # 林晓的侦查 55
    ctx = describe_result(result, "侦查")
    print(f"检定: {ctx}")

    answer = await run_turn(client, session, action, dice_context=ctx)
    print(f"\nKP: {answer}")

    # ═══ 第 3 轮 ═══
    print("\n" + "=" * 60)
    print("[第3轮]")
    action = "王博士走向那个书架，手指抚过书脊，突然停住：'等等……这本书的封皮是新的，但里面的纸页很旧。有人在掩饰什么。'"
    print(f"玩家: {action}")

    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 4 轮：测试记忆 ═══
    print("\n" + "=" * 60)
    print("[第4轮] 测试记忆——KP 应该记得之前的发现")
    action = "陈明走到王博士身边，压低声音：'你刚才说那本书有古怪？让我看看——'他伸手去拿那本封皮异常的书。"
    print(f"玩家: {action}")

    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 第 5 轮 ═══
    print("\n" + "=" * 60)
    print("[第5轮]")
    action = "林晓突然举起相机对准书架后方：'等等——那里有人！'闪光灯亮起的瞬间，一个黑影迅速闪进了更深的走廊。"
    print(f"玩家: {action}")

    answer = await run_turn(client, session, action)
    print(f"\nKP: {answer}")

    # ═══ 持久化 ═══
    session.persist()
    print("\n" + "=" * 60)
    print(session.summary())
    print("Session 状态已保存到 data/sessions/default/")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
