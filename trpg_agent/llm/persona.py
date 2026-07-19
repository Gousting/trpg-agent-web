"""构建 KP 系统 prompt，从分层 persona 文件加载。

分层结构：通用 KP 核心（prompts/kp_core_zh.md）+ 可选的战役基调覆盖（prompts/campaign_tone_zh.md）。
核心文件是系统和设定无关的；切换战役只需换覆盖层。

完整 prompt 顺序：KP 核心 → 战役基调 → 前情提要 → JSON 状态 → RAG → 近期历史。
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_CORE = _PROMPTS_DIR / "kp_core_zh.md"
_OVERLAY = _PROMPTS_DIR / "campaign_tone_zh.md"


def load_system_prompt(
    core_path: Path | None = None,
    overlay_path: Path | None = None,
) -> str:
    """加载 KP 核心人格 + 可选战役基调，拼接为完整系统 prompt。

    每次调用重新读文件，修改 prompt 后下次 DM 回合即刻生效，无需重启。
    overlay_path 传 None 或不存在的路径时只返回核心。
    """
    core_file = core_path or _CORE
    core = core_file.read_text(encoding="utf-8").strip()

    overlay_file = overlay_path or _OVERLAY
    if overlay_file.exists():
        overlay = overlay_file.read_text(encoding="utf-8").strip()
        return f"{core}\n\n--- 战役基调 ---\n\n{overlay}"

    return core
