"""Phase 1 全链路集成测试 — 模拟一次 COC 跑团对话。

用法: uv run python tests/test_pipeline.py
前提: Ollama 运行中，且已 pull 模型（默认 qwen2.5:7b）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.llm.client import OllamaClient
from trpg_agent.llm.persona import load_system_prompt
from trpg_agent.llm.prompt_assembly import assemble_system_prompt
from trpg_agent.llm.sanitize import _sanitize
from trpg_agent.rules.coc import resolve_coc, describe_result

OLLAMA_HOST = "http://192.168.0.108:11434"
MODEL = "gemma4:12b"


async def run_turn(
    client: OllamaClient,
    system: str,
    player_input: str,
    context: str = "",
) -> str:
    """执行一轮 KP 回合。"""
    user_msg = player_input
    if context:
        user_msg = f"[检定结果] {context}\n\n[调查员行动] {player_input}"

    raw = await client.chat(system, [{"role": "user", "content": user_msg}])
    return _sanitize(raw)


async def main():
    print(f"连接 Ollama: {OLLAMA_HOST}")
    client = OllamaClient(host=OLLAMA_HOST, model=MODEL, num_ctx=4096)

    # 加载 KP 人格
    persona = load_system_prompt()
    print(f"KP 人格: {len(persona)} 字")

    # 初始场景
    scene = (
        "当前场景：阿卡姆市立图书馆，深夜。\n"
        "调查员：陈明（私家侦探）、林晓（记者）、王博士（考古学家）。\n"
        "已有线索：三人收到匿名信，指向图书馆地下室的一本禁书。"
    )

    system = assemble_system_prompt(
        persona=persona,
        state_summary=scene,
    )

    print(f"\n系统 prompt: {len(system)} 字")
    print("=" * 60)

    # === 第一轮：玩家描述行动 ===
    print("\n[第1轮]")
    action = "陈明推开图书馆的旧木门，手电筒的光扫过积灰的书架。他低声说：'分头找，注意任何跟神秘学有关的东西。'"
    print(f"玩家: {action}")

    answer = await run_turn(client, system, action)
    print(f"\nKP: {answer}")

    # === 第二轮：带检定 ===
    print("\n" + "=" * 60)
    print("[第2轮] 带检定")
    action2 = "林晓蹲下来检查地板上的拖痕：'这些痕迹很新，而且一直延伸到那面墙——'她指向阅览室最里面的书架。"
    print(f"玩家: {action2}")

    # 模拟检定
    result = resolve_coc(60, "常规")  # 林晓的侦查 60
    context2 = describe_result(result, "侦查")
    print(f"检定: {context2}")

    answer2 = await run_turn(client, system, action2, context2)
    print(f"\nKP: {answer2}")

    # === 第三轮：NPC 互动 ===
    print("\n" + "=" * 60)
    print("[第3轮]")
    action3 = "王博士走向那个书架，手指抚过书脊：'等等……这本书的封皮是新的，但里面的纸页很旧。有人在掩饰什么。'"
    print(f"玩家: {action3}")

    answer3 = await run_turn(client, system, action3)
    print(f"\nKP: {answer3}")

    print("\n" + "=" * 60)
    print("全链路测试完成 ✓")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
