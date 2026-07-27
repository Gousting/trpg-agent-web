"""Session recaps — the narrative-thread half of memory (architecture §7b, golden rule #3).

The LLM summarises the session into a recap; code stores it in world state and re-injects it at the
front of the next session's prompt. This keeps the story coherent across restarts without dragging
the whole raw history along.

This module only builds recap prompts and recap input text. The actual LLM call is handled by the
runtime layer that owns the Ollama client and the per-channel history.
"""

from __future__ import annotations

# Chinese: the recap is game content (play language), like the persona. It must read like a short
# "previously on …" for the players, not a meta report — facts only, no rules talk, no commentary.
RECAP_SYSTEM_ZH = (
    "你是桌面角色扮演游戏的主持人，正在写一份简短的本场游戏摘要——"
    "一份「前情提要」，让队伍下次继续时回忆起进度。\n\n"
    "规则：\n"
    "- 写4-8句话，紧凑散文，过去时态，用中文。\n"
    "- 只写实际发生的事：参观的地点、遇到的NPC、队伍的决定、战斗及其结果、未解决的线索。\n"
    "- 用名字称呼角色。\n"
    "- 不要骰子/规则术语，不要元评论，不要对玩家说话，不要项目符号。不要编造——只总结给定的过程。\n"
    "- 以继续推进的未解决线索结尾。"
)


def build_recap_user(history: list[dict[str, str]], prior_recap: str = "") -> str:
    """Render per-channel chat history into a transcript for the summariser.

    Player turns are labelled ``玩家`` and KP narration ``主持人``; result lines fed back into the
    turn are kept because they mark what happened mechanically.

    ``prior_recap`` makes the recap cumulative: when running history is about to be cleared, an
    earlier recap already covers what scrolled out. Feeding it back in ensures the new recap
    supersedes and extends the old one instead of losing previously summarised content.
    """
    lines: list[str] = []
    for msg in history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        speaker = "主持人" if role == "assistant" else "玩家"
        lines.append(f"{speaker}: {content}")
    transcript = "\n".join(lines)
    prior_recap = (prior_recap or "").strip()
    if prior_recap:
        # The earlier recap is the "so far" the new summary must keep, then the fresh transcript is
        # what happened since. One combined recap comes back out, replacing the old one.
        return (
            "之前的摘要（「前情提要」）：\n"
            f"{prior_recap}\n\n"
            "此后发生了以下内容：\n\n"
            f"{transcript}\n\n"
            "写一份连贯的、更新后的摘要，将之前的内容和新内容合并——之前的摘要中不能丢失任何内容。"
        )
    return (
        "以下是本场游戏的记录。将其总结为「前情提要」：\n\n"
        f"{transcript}"
    )
