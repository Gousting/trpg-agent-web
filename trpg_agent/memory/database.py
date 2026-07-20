"""SQLite 持久层 — 替换 JSON 文件存储，支持调查员跨 session 复用。

数据库位置：项目 data/ 目录下的 trpg.db
特性：单文件、零依赖、WAL 模式并发安全、自动迁移旧 JSON 数据
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_state import GameState, Investigator, Npc, Quest

log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trpg.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS investigators (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    hp          INTEGER NOT NULL DEFAULT 10,
    max_hp      INTEGER NOT NULL DEFAULT 10,
    san         INTEGER NOT NULL DEFAULT 50,
    max_san     INTEGER NOT NULL DEFAULT 50,
    luck        INTEGER NOT NULL DEFAULT 50,
    skills      TEXT NOT NULL DEFAULT '{}',     -- JSON {skill: value}
    conditions  TEXT NOT NULL DEFAULT '[]',     -- JSON [condition, ...]
    inventory   TEXT NOT NULL DEFAULT '[]',     -- JSON [item, ...]
    voice_id    TEXT DEFAULT NULL,               -- 声纹标识（CAM++ speaker label）
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,
    adventure_id    TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    scene_id        TEXT NOT NULL DEFAULT '',
    recap           TEXT NOT NULL DEFAULT '',
    turn_count      INTEGER NOT NULL DEFAULT 0,
    resolved_elts   TEXT NOT NULL DEFAULT '[]',  -- JSON array
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_investigators (
    session_id      TEXT NOT NULL,
    investigator_name TEXT NOT NULL,
    joined_at       TEXT NOT NULL,
    PRIMARY KEY (session_id, investigator_name),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (investigator_name) REFERENCES investigators(name)
);

CREATE TABLE IF NOT EXISTS npcs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    attitude    TEXT NOT NULL DEFAULT 'neutral',
    description TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_npcs_session ON npcs(session_id);

CREATE TABLE IF NOT EXISTS quests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_quests_session ON quests(session_id);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    turn        INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    speaker     TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_history_session ON history(session_id);
CREATE INDEX IF NOT EXISTS idx_history_turn ON history(session_id, turn);
"""


class Database:
    """SQLite 数据库，管理所有持久化数据。"""

    def __init__(self, db_path: Path | None = None):
        self._path = db_path or _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")  # 5s BUSY 等待
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        log.debug("数据库已打开: %s", self._path)

    def close(self) -> None:
        self._conn.close()

    # ── 调查员 ────────────────────────────────────────

    def save_investigator(self, inv, *, voice_id: str | None = None) -> None:
        """插入或更新调查员（以 name 为唯一键）。voice_id 用于声纹匹配。"""
        now = _now()
        self._conn.execute(
            """INSERT INTO investigators (name, hp, max_hp, san, max_san, luck,
               skills, conditions, inventory, voice_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
               hp=excluded.hp, max_hp=excluded.max_hp,
               san=excluded.san, max_san=excluded.max_san, luck=excluded.luck,
               skills=excluded.skills, conditions=excluded.conditions,
               inventory=excluded.inventory, voice_id=excluded.voice_id,
               updated_at=excluded.updated_at""",
            (inv.name, inv.hp, inv.max_hp, inv.san, inv.max_san, inv.luck,
             json.dumps(inv.skills, ensure_ascii=False),
             json.dumps(inv.conditions, ensure_ascii=False),
             json.dumps(inv.inventory, ensure_ascii=False),
             voice_id, now, now),
        )
        self._conn.commit()

    def load_investigator(self, name: str):
        """加载单个调查员。未找到返回 None。"""
        from .game_state import Investigator
        row = self._conn.execute(
            "SELECT * FROM investigators WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return Investigator(
            name=row["name"],
            hp=row["hp"],
            max_hp=row["max_hp"],
            san=row["san"],
            max_san=row["max_san"],
            luck=row["luck"],
            skills=_json_dict(row["skills"]),
            conditions=_json_list(row["conditions"]),
            inventory=_json_list(row["inventory"]),
        )

    def list_investigators(self) -> list[str]:
        """列出所有已保存的调查员名字。"""
        rows = self._conn.execute(
            "SELECT name FROM investigators ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def find_investigator_by_voice(self, voice_id: str):
        """通过声纹标识查找调查员。Phase 5 语音链路接入时使用。"""
        from .game_state import Investigator
        row = self._conn.execute(
            "SELECT * FROM investigators WHERE voice_id = ?", (voice_id,)
        ).fetchone()
        if row is None:
            return None
        return Investigator(
            name=row["name"],
            hp=row["hp"], max_hp=row["max_hp"],
            san=row["san"], max_san=row["max_san"], luck=row["luck"],
            skills=_json_dict(row["skills"]),
            conditions=_json_list(row["conditions"]),
            inventory=_json_list(row["inventory"]),
        )

    def bind_voice(self, inv_name: str, voice_id: str) -> bool:
        """将调查员与声纹标识绑定。"""
        cur = self._conn.execute(
            "UPDATE investigators SET voice_id = ?, updated_at = ? WHERE name = ?",
            (voice_id, _now(), inv_name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_investigator(self, name: str) -> bool:
        """删除调查员。"""
        cur = self._conn.execute("DELETE FROM investigators WHERE name = ?", (name,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── Session ───────────────────────────────────────

    def create_session(self, session_id: str, adventure_id: str = "") -> None:
        """创建或覆盖 session（幂等）。"""
        self._conn.execute(
            """INSERT INTO sessions (session_id, adventure_id, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET adventure_id=excluded.adventure_id""",
            (session_id, adventure_id, _now()),
        )
        self._conn.commit()

    def save_session_state(self, state) -> None:
        """将 GameState 写入 session 行（UPSERT）。"""
        from .game_state import GameState
        assert isinstance(state, GameState)
        self._conn.execute(
            """INSERT INTO sessions
               (session_id, adventure_id, location, scene_id, recap, turn_count, resolved_elts, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
               adventure_id=excluded.adventure_id,
               location=excluded.location, scene_id=excluded.scene_id,
               recap=excluded.recap, turn_count=excluded.turn_count,
               resolved_elts=excluded.resolved_elts""",
            (state.session_id, state.adventure_id,
             state.location, state.scene_id, state.recap, state.turn_count,
             json.dumps(sorted(state.resolved_elements), ensure_ascii=False),
             _now()),
        )
        self._conn.commit()

    def load_session_state(self, session_id: str):
        """从 session 行恢复 GameState。"""
        from .game_state import GameState, Investigator, Npc, Quest
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        state = GameState(
            session_id=row["session_id"],
            system="coc_7e",
            location=row["location"],
            scene_id=row["scene_id"],
            adventure_id=row["adventure_id"],
            resolved_elements=set(_json_list(row["resolved_elts"])),
            recap=row["recap"],
            turn_count=row["turn_count"],
        )
        # 加载关联的调查员
        inv_rows = self._conn.execute(
            """SELECT i.* FROM investigators i
               JOIN session_investigators si ON si.investigator_name = i.name
               WHERE si.session_id = ?""", (session_id,)
        ).fetchall()
        for r in inv_rows:
            state.investigators.append(Investigator(
                name=r["name"], hp=r["hp"], max_hp=r["max_hp"],
                san=r["san"], max_san=r["max_san"], luck=r["luck"],
                skills=_json_dict(r["skills"]),
                conditions=_json_list(r["conditions"]),
                inventory=_json_list(r["inventory"]),
            ))
        # 加载 NPC
        npc_rows = self._conn.execute(
            "SELECT * FROM npcs WHERE session_id = ?", (session_id,)
        ).fetchall()
        for r in npc_rows:
            state.npcs.append(Npc(
                name=r["name"], attitude=r["attitude"],
                description=r["description"], location=r["location"],
            ))
        # 加载任务
        quest_rows = self._conn.execute(
            "SELECT * FROM quests WHERE session_id = ?", (session_id,)
        ).fetchall()
        for r in quest_rows:
            state.quests.append(Quest(title=r["title"], status=r["status"]))
        return state

    def add_investigator_to_session(self, session_id: str, inv_name: str) -> None:
        """将调查员加入当前 session。"""
        self._conn.execute(
            """INSERT OR IGNORE INTO session_investigators
               (session_id, investigator_name, joined_at)
               VALUES (?, ?, ?)""",
            (session_id, inv_name, _now()),
        )
        self._conn.commit()

    def remove_investigator_from_session(self, session_id: str, inv_name: str) -> None:
        """从 session 中移除调查员（不删除调查员本身）。"""
        self._conn.execute(
            "DELETE FROM session_investigators WHERE session_id = ? AND investigator_name = ?",
            (session_id, inv_name),
        )
        self._conn.commit()

    def save_session_npcs(self, session_id: str, npcs: list) -> None:
        """全量替换 session 的 NPC 列表。"""
        self._conn.execute("DELETE FROM npcs WHERE session_id = ?", (session_id,))
        for npc in npcs:
            self._conn.execute(
                """INSERT INTO npcs (session_id, name, attitude, description, location)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, npc.name, npc.attitude, npc.description, npc.location),
            )
        self._conn.commit()

    def save_session_quests(self, session_id: str, quests: list) -> None:
        """全量替换 session 的任务列表。"""
        self._conn.execute("DELETE FROM quests WHERE session_id = ?", (session_id,))
        for q in quests:
            self._conn.execute(
                "INSERT INTO quests (session_id, title, status) VALUES (?, ?, ?)",
                (session_id, q.title, q.status),
            )
        self._conn.commit()

    # ── 对话历史 ──────────────────────────────────────

    def append_history(self, session_id: str, turn: int, role: str,
                       content: str, speaker: str | None = None) -> None:
        """追加一条对话历史。"""
        self._conn.execute(
            """INSERT INTO history (session_id, turn, role, content, speaker, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, turn, role, content, speaker, _now()),
        )
        self._conn.commit()

    def load_history(self, session_id: str) -> list[dict[str, str]]:
        """加载 session 的全部对话历史（按 turn 排序）。"""
        rows = self._conn.execute(
            "SELECT role, content, speaker FROM history WHERE session_id = ? ORDER BY turn, id",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            entry: dict = {"role": r["role"], "content": r["content"]}
            if r["speaker"]:
                entry["speaker"] = r["speaker"]
            result.append(entry)
        return result

    def clear_history(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM history WHERE session_id = ?", (session_id,))
        self._conn.commit()

    def count_history(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM history WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    # ── 存档槽 ────────────────────────────────────────

    def list_saves(self, session_id: str) -> list[str]:
        """列出一个 session 的所有命名存档。"""
        prefix = f"save_{_safe_table_name(session_id)}_"
        rows = self._conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name LIKE ?""",
            (f"{prefix}%",),
        ).fetchall()
        # 从表名中提取存档名（去重）
        saves: set[str] = set()
        for r in rows:
            suffix = r[0][len(prefix):]
            for table_type in ("_investigators", "_session", "_history", "_npcs", "_quests"):
                if suffix.endswith(table_type):
                    saves.add(suffix[:-len(table_type)])
                    break
        return sorted(saves)

    def save_snapshot(self, session_id: str, name: str) -> None:
        """创建命名存档快照——将当前 session 所有数据复制到独立表。

        同名快照会先删除旧数据再创建（覆盖语义）。
        """
        safe_sid = _safe_table_name(session_id)
        safe_name = _safe_table_name(name)
        pfx = f"save_{safe_sid}_{safe_name}"

        # 先删旧快照（覆盖语义）
        for suffix in ("_investigators", "_session", "_history", "_npcs", "_quests"):
            self._conn.execute(f"DROP TABLE IF EXISTS {pfx}{suffix}")

        self._conn.execute(
            f"""CREATE TABLE {pfx}_investigators AS
            SELECT i.* FROM investigators i
            JOIN session_investigators si ON si.investigator_name = i.name
            WHERE si.session_id = ?""",
            (session_id,),
        )
        self._conn.execute(
            f"CREATE TABLE {pfx}_session AS SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        self._conn.execute(
            f"CREATE TABLE {pfx}_history AS SELECT * FROM history WHERE session_id = ?",
            (session_id,),
        )
        self._conn.execute(
            f"CREATE TABLE {pfx}_npcs AS SELECT * FROM npcs WHERE session_id = ?",
            (session_id,),
        )
        self._conn.execute(
            f"CREATE TABLE {pfx}_quests AS SELECT * FROM quests WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()
        log.info("快照存档: %s/%s", session_id, name)

    def load_snapshot(self, session_id: str, name: str):
        """从快照恢复 session 状态，返回 GameState + history list。"""
        safe_sid = _safe_table_name(session_id)
        safe_name = _safe_table_name(name)
        pfx = f"save_{safe_sid}_{safe_name}"

        # 检查快照是否存在
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (f"{pfx}_session",),
        ).fetchone()
        if row is None:
            return None

        from .game_state import GameState, Investigator, Npc, Quest

        # 恢复 session 行
        sess_row = self._conn.execute(
            f"SELECT * FROM {pfx}_session"
        ).fetchone()
        if sess_row is None:
            return None

        state = GameState(
            session_id=sess_row["session_id"],
            system="coc_7e",
            location=sess_row["location"],
            scene_id=sess_row["scene_id"],
            adventure_id=sess_row["adventure_id"],
            resolved_elements=set(_json_list(sess_row["resolved_elts"])),
            recap=sess_row["recap"],
            turn_count=sess_row["turn_count"],
        )

        # 恢复调查员
        for r in self._conn.execute(f"SELECT * FROM {pfx}_investigators"):
            state.investigators.append(Investigator(
                name=r["name"], hp=r["hp"], max_hp=r["max_hp"],
                san=r["san"], max_san=r["max_san"], luck=r["luck"],
                skills=_json_dict(r["skills"]),
                conditions=_json_list(r["conditions"]),
                inventory=_json_list(r["inventory"]),
            ))

        # 恢复 NPC
        for r in self._conn.execute(f"SELECT * FROM {pfx}_npcs"):
            state.npcs.append(Npc(
                name=r["name"], attitude=r["attitude"],
                description=r["description"], location=r["location"],
            ))

        # 恢复 quests
        for r in self._conn.execute(f"SELECT * FROM {pfx}_quests"):
            state.quests.append(Quest(title=r["title"], status=r["status"]))

        # 恢复 history
        history = []
        for r in self._conn.execute(
            f"SELECT role, content, speaker FROM {pfx}_history ORDER BY turn, id"
        ):
            entry: dict = {"role": r["role"], "content": r["content"]}
            if r["speaker"]:
                entry["speaker"] = r["speaker"]
            history.append(entry)

        log.info("快照读档: %s/%s (第 %d 轮)", session_id, name, state.turn_count)
        return state, history

    def delete_snapshot(self, session_id: str, name: str) -> bool:
        """删除命名快照。"""
        safe_sid = _safe_table_name(session_id)
        safe_name = _safe_table_name(name)
        pfx = f"save_{safe_sid}_{safe_name}"
        tables = ["_investigators", "_session", "_history", "_npcs", "_quests"]
        for suffix in tables:
            self._conn.execute(f"DROP TABLE IF EXISTS {pfx}{suffix}")
        self._conn.commit()
        return True

    # ── 迁移 ──────────────────────────────────────────

    def migrate_json_save(self, session_id: str, save_name: str) -> bool:
        """从旧 JSON 格式存档迁移到数据库快照。"""
        from .game_state import GameState
        save_dir = _DB_PATH.parent / "saves" / session_id / save_name
        state_path = save_dir / "state.json"
        if not state_path.is_file():
            return False

        state = GameState.load(state_path)
        if state is None:
            return False

        # 写入当前 session
        self.save_session_state(state)
        for inv in state.investigators:
            self.save_investigator(inv)
            self.add_investigator_to_session(session_id, inv.name)
        self.save_session_npcs(session_id, state.npcs)
        self.save_session_quests(session_id, state.quests)

        # 迁移对话历史
        history_path = save_dir / "history.jsonl"
        if history_path.is_file():
            for turn_idx, line in enumerate(
                history_path.read_text(encoding="utf-8").splitlines()
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self.append_history(
                        session_id, turn_idx,
                        rec.get("role", "user"),
                        rec.get("content", ""),
                        rec.get("speaker"),
                    )
                except (ValueError, KeyError):
                    continue

        # 创建快照
        self.save_snapshot(session_id, save_name)
        log.info("迁移完成: %s/%s", session_id, save_name)
        return True


# ── 工具函数 ────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_table_name(name: str) -> str:
    """校验并返回安全的表名后缀（仅允许字母数字下划线）。"""
    sanitized = name.replace("-", "_").replace(" ", "_")
    if not sanitized or not sanitized.replace("_", "").isalnum():
        raise ValueError(f"非法表名: {name!r}")
    return sanitized


def _json_field(raw) -> list | dict:
    """安全解析 JSON 字段。"""
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [] if isinstance(raw, str) and raw.startswith("[") else {}


def _json_list(raw) -> list:
    """安全解析 JSON 列表字段。"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return val if isinstance(val, list) else []
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _json_dict(raw) -> dict:
    """安全解析 JSON 字典字段。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


# 全局单例（惰性初始化，线程安全）
import threading

_db: Database | None = None
_db_lock = threading.Lock()


def get_db(db_path: Path | None = None) -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:  # double-check
                _db = Database(db_path)
    return _db
