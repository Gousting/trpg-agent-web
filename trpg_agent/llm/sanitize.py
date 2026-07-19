"""KP 回答清洗管道 — 去掉小模型在中文 COC 跑团中的常见毛病。

纯正则（无外部依赖），所以可以独立测试。从 DMbot 的德语 sanitize.py 重写为中文版本。
覆盖：角色标签前缀、元话语开场白、结尾催促问句、自纠正框架、括号内元注释、AI 自指、
英文标点混杂、过渡词水句。

所有函数保持与 DMbot 原版相同的签名，以兼容 stream_assembler 和 orchestrator。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 模式：角色标签（开头）
# 小模型有时会在回答前加 "守秘人：" / "KP：" / "DM：" 等标签，TTS 会直接念出来。
# ---------------------------------------------------------------------------
_ROLE_LABEL = re.compile(
    r"^\s*(?:守秘人|kp|keeper|gm|dm|主持人|游戏主持人|地下城主|ai助手|ai)\s*[：:]\s*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 模式：元话语开场白
# "作为守秘人，我描述一下..." / "好的，让我为你叙述..." / "现在让KP来..."
# 德语版依赖 "Als <rolle> <verb> ich" 的句法锚点。中文没有这个结构，
# 改为匹配常见的元话语模式——"作为XX" + "我/让我" + 描述类动词。
# 结尾的逗号/冒号连同后面紧跟的引号一起吞掉。
# ---------------------------------------------------------------------------
_META_PREAMBLE = re.compile(
    r"^\s*"
    # 可选的引导词："好的，" / "现在，" / "那么，"
    r"(?:(?:好的|现在|那么|OK|ok)\s*[，,]?\s*)?"
    r"(?:"
    # 模式 A: "作为KP/守秘人，我/让我来 描述/叙述..."
    r"作为(?:(?:一位|一名|个)\s*)?(?:守秘人|KP|Keeper|游戏主持人|主持人|GM|DM|地下城主)"
    r"[，,\s]*"
    r"(?:我(?:来|就|先|先来)?|让我(?:们|来)?|我们就?|我们(?:来|就)?)"
    r"[，,\s]*"
    r"(?:为(?:大家|你(?:们)?|你们)|给大家)?"
    r"[，,\s]*"
    r"(?:描述|叙述|讲述|开始|描绘|介绍|说明|交代|呈现|展开)"
    r"|"
    # 模式 B: "让我/我来 描述/叙述..."（不需要 "作为KP" 前缀）
    r"(?:我(?:来|就|先|先来)|让我(?:们|来)?)"
    r"[，,\s]*"
    r"(?:为(?:大家|你(?:们)?|你们)|给大家)?"
    r"[，,\s]*"
    r"(?:描述|叙述|讲述|描绘|介绍|说明|交代|呈现|展开)"
    r")"
    r"(?:一下|描述|叙述|讲述|场景|剧情|故事|当前状况|目前状况|此时状况)?"
    r"[，,\s]*"
    # 可选的连接词
    r"(?:如下|这样|如此|以下|的方式)?"
    r"[：:，,。]?\s*"
    # 紧跟的引号也要吞掉
    r"[「『\"]?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 模式：结尾括号内的元注释
# "(请注意，作为AI我不会替调查员做决定...)" 这类提示。
# 只在括号内容含元话语关键词时才匹配，保护真正的扮演内容 "(一声枪响)"。
# ---------------------------------------------------------------------------
_META_PAREN = re.compile(
    r"\s*[（(]"
    r"(?=[^）)]*"
    r"(?:"
    # 中文元话语关键词（无 \b，中文字符间没有 word boundary）
    r"注意|提醒|提示|说明|注释|注[：:：]|以上|以下|请根|根据实际"
    r"|仅供参考|未完待续|此处[可应]|建议|可选"
    r"|"
    # 英文/混合关键词（保留 \b）
    r"\b(?:AI助手|仅供参考)\b"
    r")"
    r")"
    r"[^）)]*"
    r"[）)]\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 模式：结尾的催促问句
# 小模型几乎每轮结尾都加 "你们要怎么做？" / "请做出你的选择。" 等。
# 只匹配结尾的泛用催促，NPC 角色的真实提问不会被误伤（NPC 提问由 consistency guard 处理）。
# ---------------------------------------------------------------------------
_TRAILING_PROMPT = re.compile(
    r"\s*"
    r"(?:你们|你|大家|各位)"
    r"\s*"
    r"(?:要|打算|准备|想|会|将)"
    r"\s*"
    r"(?:怎么(?:做|办|行动)|如何(?:做|行动|应对|处理)|怎样|干嘛|干什么)"
    r"[？?]\s*$"
    r"|"
    r"\s*"
    r"(?:请|请你们|请各位?)\s*"
    r"(?:做出?(?:你(?:们)?的?)?选择|告诉我(?:你(?:们)?的?)?行动|决定(?:你(?:们)?的?)?下一步)"
    r"[。！？.!?]?\s*$"
    r"|"
    r"\s*"
    r"(?:现在|那么|接下来)\s*"
    r"(?:轮到(?:你(?:们)?|大家)|到(?:你(?:们)?|大家))"
    r"(?:了)?\s*[。！？.!?]?\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 模式：自纠正框架
# "以下是正确的回答：" / "修正后的回答：" / "让我重新组织一下："
# 删掉前面的元内容，只保留真正的叙述。
# ---------------------------------------------------------------------------
_META_SELFCORRECT = re.compile(
    r"^.*?\b"
    r"(?:以下是(?:正确的|修正后的?|重新组织的?|调整后的?)?(?:回答|版本|叙述|描述)"
    r"|修正(?:后的?)?(?:回答|版本)"
    r"|让我重新(?:组织|来|整理)"
    r"|重新来[过了]?"
    r"|补充说明[：:]"
    r")"
    r"\s*[：:]\s*",
    re.IGNORECASE | re.DOTALL,
)

# ---------------------------------------------------------------------------
# 模式：AI 自指
# "作为AI语言模型..." / "我是一个AI助手..." / "因为我是AI..."
# 这些绝对不能出现在 TTS 输出中。只匹配 AI 自指的句子，不匹配游戏内的 AI 角色。
# ---------------------------------------------------------------------------
_AI_SELFREF = re.compile(
    r"(?:"
    r"作为(?:一个?|一名?)?\s*(?:AI|人工智能|语言模型|大语言模型|大模型|AI助手)"
    r"|我(?:是|只是|只)\s*(?:一个?|一名?)?\s*(?:AI|人工智能|语言模型|大语言模型|AI助手|程序)"
    r"|因为(?:我|我们)\s*(?:是|只是)\s*(?:AI|人工智能|语言模型|程序)"
    r")\s*"
    r"[，,。.!！]?\s*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 模式：过渡词水句
# 中文小模型爱在句首加 "首先，" / "接下来，" / "最后，" / "综上所述，"
# 在口语化 KP 叙述中这些词很出戏。移除开头的过渡词。
# ---------------------------------------------------------------------------
_TRANSITIONAL_FLUFF = re.compile(
    r"^\s*"
    r"(?:首先[，,]?\s*|接下来[，,]?\s*|然后[，,]?\s*|紧接着[，,]?\s*"
    r"|最后[，,]?\s*|总而言之[，,]?\s*|综上所述[，,]?\s*"
    r"|总的来[说讲][，,]?\s*|需要注意的[是][，,]?\s*"
    r"|值得注意的[是][，,]?\s*|值得一提的[是][，,]?\s*)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 角色标签列表（用于 cut_at_labels / strip_leading_label 及 stop sequences）
# ---------------------------------------------------------------------------
_ROLE_LABELS = [
    "守秘人", "KP", "Keeper", "GM", "DM",
    "主持人", "游戏主持人", "地下城主",
    "玩家", "调查员", "Player",
    "AI", "AI助手",
]


def _cut_at_labels(text: str, labels: list[str]) -> str:
    """截断第一个出现在正文中间的 ``<label>：`` —— 模型开始编造下一个说话者。

    位置 0 的标签留给 ``_strip_leading_label`` 处理。
    """
    cut = len(text)
    for label in labels:
        for sep in ("：", ":"):
            idx = text.find(f"{label}{sep}")
            if 0 < idx < cut:
                cut = idx
    return text[:cut].strip()


def _strip_leading_label(text: str, labels: list[str]) -> str:
    """去掉开头的 ``<label>：`` 前缀（模型在回答开头给自己加的角色标签）。

    大小写不敏感。只匹配精确开头，不会误伤叙述中的冒号。
    """
    for label in labels:
        for sep in ("：", ":"):
            prefix = f"{label}{sep}"
            if text[:len(prefix)].lower() == prefix.lower():
                return text[len(prefix):].lstrip()
    return text


def _strip_meta_preamble(text: str) -> str:
    """去掉开头的 "作为守秘人，我描述一下..." 元话语开场白。"""
    m = _META_PREAMBLE.match(text)
    if not m or m.end() == 0:
        return text
    return text[m.end():].lstrip()


def _strip_ai_selfref(text: str) -> str:
    """移除 "作为AI语言模型..." / "我是一个AI助手..." 自指。逐句处理。"""
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    cleaned = [s for s in sentences if not _AI_SELFREF.match(s.strip())]
    return "".join(cleaned) if cleaned else text


def _strip_transitional_fluff(text: str) -> str:
    """移除句首过渡词水句。"""
    return _TRANSITIONAL_FLUFF.sub("", text).lstrip()


# 中文全角引号对 + 英文引号对
_ENCLOSING_QUOTES = (
    ('"', '"'),
    ("'", "'"),
    ("「", "」"),
    ("『", "』"),
    ("„", "\""),
    ("\u201c", "\u201d"),  # " "
)


def _unwrap_enclosing_quotes(text: str) -> str:
    """去掉包裹整段回答的一对引号。

    只有最外层且闭合引号不在正文中再次出现时才剥掉——避免破坏 NPC 对话引号。
    """
    t = text.strip()
    if len(t) < 2:
        return text
    for open_q, close_q in _ENCLOSING_QUOTES:
        if t[0] == open_q and t[-1] == close_q and close_q not in t[1:-1]:
            return t[1:-1].strip()
    return text


def _strip_trailing_prompt(text: str) -> str:
    """去掉结尾的泛用催促问句 "你们要怎么做？" / "请做出你的选择。"。"""
    stripped = _TRAILING_PROMPT.sub("", text).strip()
    return stripped or text


def _sanitize_leading(text: str) -> str:
    """清洗前半段：markdown 标记 → 自纠正框架 → AI 自指 → 角色标签 → 元话语开场白 → 过渡词。

    拆分出来是为了让 stream_assembler 能增量应用（不涉及结尾清洗）。
    """
    text = text.replace("*", "").replace("`", "").replace("#", "").strip()
    text = _META_SELFCORRECT.sub("", text, count=1).strip()
    text = _strip_ai_selfref(text)
    text = _ROLE_LABEL.sub("", text).strip()
    text = _strip_meta_preamble(text)
    text = _strip_transitional_fluff(text)
    return text


def _sanitize_trailing(text: str) -> str:
    """清洗后半段：括号元注释 → 结尾催促问句。

    只在最终句应用，所以 stream_assembler 可以保留中间句的完整性。
    """
    text = _META_PAREN.sub("", text).strip()
    text = _strip_trailing_prompt(text)
    return text


def _sanitize(text: str) -> str:
    """完整清洗管道：前半 → 剥外层引号 → 后半。"""
    return _sanitize_trailing(_unwrap_enclosing_quotes(_sanitize_leading(text)))


# 中文句末标点（含全角和半角）
_SENTENCE_END = re.compile(r"[。！？…\.!\?](?:[」』\u201d\)）])?")


def _trim_to_last_sentence(text: str) -> str:
    """如果回答在句子中间被截断，回溯到最后一个完整句子。

    只在前一句存在且有真实后续残句时才裁切。
    全标点的完整回答不做任何改动。
    """
    ends = list(_SENTENCE_END.finditer(text))
    if not ends:
        return text
    last = ends[-1].end()
    return text[:last].strip() if text[last:].strip() else text
