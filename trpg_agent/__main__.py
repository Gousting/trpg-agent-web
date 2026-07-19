"""TRPG Agent — 中文 COC 跑团 KP，本地 AI 主持人。

Phase 1: 纯文字 DM Agent。
Phase 2: 语音链路 (FunASR + CAM++)。
Phase 3: 平板客户端 (HTML + WebSocket)。
"""

from __future__ import annotations

import asyncio
import logging

from .logsetup import setup_logging

log = logging.getLogger(__name__)


def main() -> None:
    """Entry point for `trpg` CLI."""
    setup_logging()
    log.info("TRPG Agent 启动中...")
    print("TRPG Agent v0.1.0 — 中文 COC KP")
    # TODO: Phase 1 — 跑通最小闭环


if __name__ == "__main__":
    main()
