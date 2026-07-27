"""TRPG Agent — 中文 COC 跑团 KP，本地 AI 主持人。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence

import httpx

from .adventure import Adventure
from .llm.client import OllamaClient
from .llm.sanitize import _sanitize
from .logsetup import setup_logging
from .session import Session

log = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
DEFAULT_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
DEFAULT_LOG_LEVEL = os.getenv("TRPG_LOG_LEVEL", "INFO")
_EXIT_COMMANDS = {"/quit", "/exit", "/q"}
_ADVENTURE_ALIASES = {
    "haunted_house": "haunted_house",
    "鬼屋": "haunted_house",
    "古屋疑云": "haunted_house",
}


def _resolve_adventure_id(name: str | None) -> str | None:
    if not name:
        return None
    return _ADVENTURE_ALIASES.get(name.strip(), name.strip())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="中文 COC 跑团 CLI")
    parser.add_argument("--host", default=DEFAULT_OLLAMA_HOST, help="Ollama 地址")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help="Ollama 模型名")
    parser.add_argument("--session-id", default="default", help="会话 ID")
    parser.add_argument("--adventure", help="模组 ID 或别名，例如 haunted_house / 鬼屋")
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="上下文窗口大小")
    parser.add_argument("--log-level", default=DEFAULT_LOG_LEVEL, help="日志级别")
    parser.add_argument("--check", action="store_true", help="只做离线自检，不连接 Ollama")
    parser.add_argument("--once", help="执行一轮输入后退出，便于脚本化验证")
    return parser


async def _run_turn(
    session: Session,
    client: OllamaClient,
    player_input: str,
    *,
    adventure: Adventure | None = None,
) -> tuple[str, str]:
    dice_context, _request = await session.classify_and_resolve(client, player_input)
    system = session.build_system_prompt(adventure=adventure)
    messages = session.build_messages(player_input, dice_context=dice_context)
    raw = await client.chat(system, messages)
    answer = _sanitize(raw)
    session.record_turn(player_input, answer)
    await session.maybe_compress(client)
    session.persist()
    return answer, dice_context


def _print_banner(session: Session, adventure: Adventure | None) -> None:
    print("TRPG Agent v0.1.0 — 中文 COC KP")
    if adventure is not None:
        scene = adventure.get_scene(session.state.scene_id)
        print(f"模组: {adventure.title} ({adventure.id})")
        if scene is not None:
            print(f"起始场景: {scene.title}")
            print(scene.description)
    print(session.summary())


async def _run_cli(args: argparse.Namespace) -> int:
    session = Session(args.session_id, max_context=args.num_ctx)
    adventure: Adventure | None = None
    if args.adventure:
        adventure_id = _resolve_adventure_id(args.adventure)
        adventure = session.load_adventure(adventure_id) if adventure_id else None
        if adventure is None:
            print(f"未找到模组: {args.adventure}")
            return 2

    _print_banner(session, adventure)
    if args.check:
        print("自检通过：CLI、日志和会话加载正常。")
        session.persist()
        return 0

    client = OllamaClient(host=args.host, model=args.model, num_ctx=args.num_ctx)
    try:
        if args.once:
            answer, dice_context = await _run_turn(session, client, args.once, adventure=adventure)
            if dice_context:
                print(f"检定: {dice_context}")
            print(f"\nKP: {answer}")
            return 0

        print("输入调查员行动开始跑团；/summary 查看状态，/save 立即保存，/quit 退出。")
        while True:
            try:
                player_input = input("\n你> ").strip()
            except EOFError:
                print()
                break

            if not player_input:
                continue
            if player_input in _EXIT_COMMANDS:
                break
            if player_input == "/summary":
                print(session.summary())
                continue
            if player_input == "/save":
                session.persist()
                print("已保存当前会话。")
                continue

            try:
                answer, dice_context = await _run_turn(
                    session,
                    client,
                    player_input,
                    adventure=adventure,
                )
            except httpx.HTTPError as exc:
                log.error("Ollama 请求失败: %s", exc)
                print(f"Ollama 请求失败: {exc}")
                continue

            if dice_context:
                print(f"检定: {dice_context}")
            print(f"\nKP: {answer}")
        return 0
    finally:
        session.persist()
        await client.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    setup_logging(args.log_level)
    log.info("TRPG Agent 启动中")
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
