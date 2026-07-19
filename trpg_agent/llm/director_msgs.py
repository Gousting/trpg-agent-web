"""GM-side "director" instructions for the opening turns (pure prompt text).

Extracted from ``orchestrator.py`` (ADR 034). :func:`build_opening_director_msg` drives the short
``!start`` briefing; :func:`build_intro_director_msg` drives the longer ``!intro`` monologue
(ADR 031) and embeds the party roster. The DM never reads these aloud — they instruct the model to
OPEN the session. The cog (``dmcog.py``) imports these; nothing in ``DMBrain`` calls them (it
receives the resulting ``director_msg`` as a parameter).
"""

from __future__ import annotations


# --- Opening briefing (!start) ------------------------------------------------------------------

# The director instruction that drives the !start opening turn. It is a GM-side ("director")
# message, NOT a player line: it tells the model to OPEN the session out loud so the table knows
# who they are and what their mission is (the first-session complaint: the bot "hat am Anfang
# nicht gesagt, was abgeht"). The concrete content — the Halikarn briefing, the three leads — is
# NOT spelled out here: it lives in the start scene's card (## Aktuelle Szene + guidance_de),
# which the system prompt already carries. So this only has to point the model at that scene and
# hold it to the persona's voice. Phrased as an instruction to the GM, never read aloud.
OPENING_DIRECTOR_MSG = (
    "[导演] 现在开启游戏：扮演你当前场景中的任务/开场场景。让玩家清楚他们是谁、任务是什么，"
    "并通过环境的某个细节暗示最初的线索——不要罗列。保持主持人语调（2-4句话）。不要要求检定。"
)


def build_opening_director_msg() -> str:
    """The GM-side director instruction for the ``!start`` opening turn (pure, unit-testable).

    Kept as a function so the cog never inlines the prompt text and a test can assert its shape
    (it must read as a GM/director instruction, not as a player action, and must forbid a dice
    test on the briefing)."""
    return OPENING_DIRECTOR_MSG


# --- Intro monologue (!intro) ------------------------------------------------------------------

# The director instruction for the one-time !intro opening MONOLOGUE (ADR 031). Unlike the short
# !start briefing (OPENING_DIRECTOR_MSG, 2–4 sentences), this asks for one coherent opening monologue
# that establishes place + how they arrived + the mission AND gives each player character a personal
# beat. The concrete adventure content (place, mission, leads) lives in the start scene's card +
# adventure summary already in the system prompt; the party roster is embedded here (it rides in the
# turn's user message so the ADR-019 prompt order is untouched). GM-side instruction, never read aloud.
_INTRO_DIRECTOR_HEAD = (
    "[导演] 现在以一个连贯的开场独白开启游戏（多段落，不要罗列，不要要点）。"
    "立即以叙述者身份切入场景——不要写你正在开启游戏或你作为主持人在做什么，不要预告独白。"
    "先确定队伍在哪里以及他们是如何到达的，然后确定局势和他们的任务——依据你的当前场景和冒险摘要。"
)
_INTRO_DIRECTOR_CHARS = (
    "然后将以下每个角色用一个简短的个人时刻引入（用名字称呼他们，"
    "联系他们的出身、性格和动机）——将其编织进画面中，不要逐字朗读，"
    "秘密或纯私人目标最多只能暗示提及：\n\n{roster}"
)
_INTRO_DIRECTOR_TAIL = (
    "全程保持主持人语调，给自己空间——这是开场，可以比普通回合长很多。"
    "以氛围感收官，邀请队伍进入场景（比如他们要先追踪哪条线索）；"
    "不要在几句之后就用一句简短的「你们怎么做？」收尾。不要要求检定。"
)


def build_intro_director_msg(roster_zh: str = "") -> str:
    """The GM-side director instruction for the ``!intro`` opening monologue (pure, unit-testable).

    Asks for one coherent opening monologue (place + how they arrived + mission) and weaves in each
    player character via the embedded ``roster_zh`` block (from ``CharacterStore.intro_roster_zh``).
    With an empty roster it degrades to the place/mission monologue alone. Kept as a function so the
    cog never inlines the prompt text and a test can assert its shape (one monologue, every figure
    involved, no dice)."""
    msg = _INTRO_DIRECTOR_HEAD
    if roster_zh.strip():
        msg += " " + _INTRO_DIRECTOR_CHARS.format(roster=roster_zh.strip())
        msg += "\n\n" + _INTRO_DIRECTOR_TAIL
    else:
        msg += " " + _INTRO_DIRECTOR_TAIL
    return msg
