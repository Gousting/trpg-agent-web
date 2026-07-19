"""Session 管理器 — 连接记忆、状态、prompt 组装到 pipeline。

职责：
1. 加载/创建游戏状态（GameState）
2. 管理对话历史（HistoryStore）
3. 上下文窗口管理（token 估算 + recap 压缩）
4. 组装完整 system prompt
5. 每轮后的持久化
"""

from __future__ import annotations

import logging
from pathlib import Path

from .memory.game_state import GameState, Investigator, Npc, Quest
from .memory.history import HistoryStore
from .llm.persona import load_system_prompt
from .llm.prompt_assembly import assemble_system_prompt

log = logging.getLogger(__name__)

# 默认 session 数据目录（项目根目录下的 data/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"

# 上下文窗口预算（token 估算：中文约 1.5 字/token）
DEFAULT_MAX_CONTEXT = 4096         # Ollama num_ctx 默认值
HISTORY_MAX_TURNS = 20             # 最多保留轮数
RECAP_TRIGGER_RATIO = 0.75         # 超过 75% 上下文时触发 recap 压缩


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数。中文约 1.5 字/token，英文约 4 字/token。"""
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


class Session:
    """管理一局 COC 跑团的完整会话状态。"""

    def __init__(
        self,
        session_id: str = "default",
        *,
        max_context: int = DEFAULT_MAX_CONTEXT,
        data_dir: Path | None = None,
    ):
        self.session_id = session_id
        self.max_context = max_context
        self._dir = data_dir or SESSIONS_DIR
        self._state_path = self._dir / session_id / "state.json"
        self._history_path = self._dir / session_id / "history.jsonl"

        # 加载或创建状态
        self.state = GameState.load(self._state_path)
        if self.state is None:
            self.state = GameState(session_id=session_id)
            log.info("新建 session: %s", session_id)
        else:
            log.info("加载 session: %s (第 %d 轮)", session_id, self.state.turn_count)

        # 加载对话历史
        self.history = HistoryStore(self._history_path)

        # 加载 KP 人格
        self._persona = load_system_prompt()

    # ── 角色管理 ────────────────────────────────────

    def load_characters(self, characters_path: Path | None = None) -> None:
        """从 JSON 文件加载调查员。

        JSON 格式：{"investigators": [{"name": "陈明", "hp": 12, ...}]}
        或直接的 list：[{"name": "陈明", ...}]

        characters_path 为 None 时先尝试 session 目录，再尝试 default 目录。
        """
        import json

        if characters_path is None:
            session_path = self._dir / self.session_id / "characters.json"
            default_path = self._dir / "default" / "characters.json"
            characters_path = session_path if session_path.is_file() else default_path

        if not characters_path.is_file():
            log.warning("角色文件不存在: %s", characters_path)
            return

        data = json.loads(characters_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            investigators = data
        else:
            investigators = data.get("investigators", [])

        for char_data in investigators:
            inv = Investigator.from_dict(char_data)
            existing = self.state.find_investigator(inv.name)
            if existing:
                existing.hp = inv.hp
                existing.max_hp = inv.max_hp
                existing.san = inv.san
                existing.max_san = inv.max_san
                existing.luck = inv.luck
                existing.skills = inv.skills
            else:
                self.state.investigators.append(inv)

        # 同时加载 NPC
        if isinstance(data, dict) and "npcs" in data:
            for npc_data in data["npcs"]:
                npc = Npc.from_dict(npc_data)
                if not self.state.find_npc(npc.name):
                    self.state.npcs.append(npc)

        # 加载任务
        if isinstance(data, dict) and "quests" in data:
            for q_data in data["quests"]:
                quest = Quest.from_dict(q_data)
                if not any(q.title == quest.title for q in self.state.quests):
                    self.state.quests.append(quest)

        log.info("加载了 %d 个调查员", len(self.state.investigators))

    # ── 上下文管理 ──────────────────────────────────

    def _system_token_budget(self) -> int:
        """计算系统 prompt 的基础 token 消耗（不含历史）。"""
        base = _estimate_tokens(self._persona) + _estimate_tokens(self.state.scene_summary())
        if self.state.recap:
            base += _estimate_tokens(self.state.recap) + 20  # "前情提要" 标题
        return base

    def _history_token_usage(self) -> int:
        """对话历史的 token 估算。"""
        total = 0
        for entry in self.history.entries():
            total += _estimate_tokens(entry.get("content", ""))
        return total

    def _should_compress(self) -> bool:
        """判断是否需要 recap 压缩。"""
        system_tokens = self._system_token_budget()
        history_tokens = self._history_token_usage()
        total = system_tokens + history_tokens
        return total > self.max_context * RECAP_TRIGGER_RATIO

    # ── Recap ─────────────────────────────────────

    def build_recap_context(self) -> str:
        """构建 recap 上下文——供 LLM 生成前情提要。"""
        if self.state.recap:
            return f"前情提要：{self.state.recap}\n\n最近对话：\n{self.history.as_text(last=10)}"
        return f"对话记录：\n{self.history.as_text(last=15)}"

    def set_recap(self, text: str) -> None:
        """设置前情提要。"""
        self.state.recap = text.strip()
        self.history.clear()  # 摘要已包含关键信息，清旧对话腾出窗口
        log.info("Recap 已更新 (%d 字), 对话历史已清理", len(self.state.recap))

    # ── Prompt 组装 ────────────────────────────────

    def build_system_prompt(self) -> str:
        """组装完整的 system prompt。"""
        return assemble_system_prompt(
            persona=self._persona,
            recap=self.state.recap if self.state.recap else None,
            state_summary=self.state.scene_summary(),
        )

    def build_messages(self, player_input: str, *, dice_context: str = "") -> list[dict[str, str]]:
        """构建发给 Ollama 的消息列表。

        Args:
            player_input: 玩家输入
            dice_context: 检定结果（可选）
        """
        messages = self.history.as_messages()

        user_msg = player_input
        if dice_context:
            user_msg = f"[检定结果] {dice_context}\n\n[调查员行动] {player_input}"

        messages.append({"role": "user", "content": user_msg})
        return messages

    # ── 回合管理 ────────────────────────────────────

    def record_turn(self, player_input: str, kp_answer: str) -> None:
        """记录一轮对话。"""
        self.history.append("user", player_input)
        self.history.append("assistant", kp_answer)
        self.state.turn_count += 1

        # 限制历史长度
        if self.history.count() > HISTORY_MAX_TURNS * 2:
            self.history.trim(keep_last=HISTORY_MAX_TURNS * 2)
            log.debug("对话历史已裁剪至 %d 条", HISTORY_MAX_TURNS * 2)

    def persist(self) -> None:
        """持久化状态和历史。"""
        self.state.save(self._state_path)
        # history 在 append 时已自动写入
        log.debug("状态已保存 (第 %d 轮)", self.state.turn_count)

    # ── 便捷方法 ────────────────────────────────────

    def summary(self) -> str:
        """Session 概览。"""
        inv_names = [i.name for i in self.state.investigators]
        npc_names = [n.name for n in self.state.npcs]
        return (
            f"Session {self.session_id} | 第 {self.state.turn_count} 轮\n"
            f"地点: {self.state.location or '未设定'}\n"
            f"调查员: {', '.join(inv_names) or '无'}\n"
            f"NPC: {', '.join(npc_names) or '无'}\n"
            f"历史: {self.history.count()} 条, ~{self._history_token_usage()} tokens\n"
            f"上下文: system ~{self._system_token_budget()} + history ~{self._history_token_usage()}"
            f" = {self._system_token_budget() + self._history_token_usage()} / {self.max_context}"
        )
