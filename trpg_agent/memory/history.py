"""对话历史包装器 — 为 Session 提供面向对象 API。

底层使用 history.py 的 JSONL 文件持久化。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

MAX_ENTRIES = 100


class HistoryStore:
    """对话历史——支持逐条追加、查询、清理。"""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._entries: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        """从 JSONL 文件恢复。"""
        if not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                role = rec.get("role")
                content = rec.get("content")
                if role and content:
                    self._entries.append({"role": role, "content": content})
            except (ValueError, KeyError):
                continue
        log.debug("从 %s 加载了 %d 条历史", self._path, len(self._entries))

    def append(self, role: str, content: str) -> None:
        """追加一条，并写入文件。"""
        ts = datetime.now(timezone.utc).isoformat()
        record = {"ts": ts, "role": role, "content": content}
        self._entries.append({"role": role, "content": content})

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 上限裁剪
        while len(self._entries) > MAX_ENTRIES:
            self._entries.pop(0)

    def entries(self) -> list[dict[str, str]]:
        """所有条目。"""
        return list(self._entries)

    def as_messages(self) -> list[dict[str, str]]:
        """转为 Ollama 消息格式。"""
        return [{"role": e["role"], "content": e["content"]} for e in self._entries]

    def as_text(self, last: int = 0) -> str:
        """转为纯文本。last=0 返回全部。"""
        entries = self._entries[-last:] if last > 0 else self._entries
        lines = []
        for e in entries:
            prefix = "玩家" if e["role"] == "user" else "KP"
            lines.append(f"{prefix}: {e['content']}")
        return "\n".join(lines)

    def clear(self) -> None:
        """清空历史（文件保留但截断）。"""
        self._entries.clear()
        self._path.write_text("", encoding="utf-8")

    def count(self) -> int:
        return len(self._entries)

    def trim(self, keep_last: int) -> None:
        """保留最后 N 条。"""
        if len(self._entries) > keep_last:
            self._entries = self._entries[-keep_last:]
            # 重写文件
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as fh:
                for e in self._entries:
                    ts = datetime.now(timezone.utc).isoformat()
                    rec = {"ts": ts, "role": e["role"], "content": e["content"]}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
