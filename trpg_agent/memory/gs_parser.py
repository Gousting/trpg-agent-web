"""GS 标记块解析器 — 提取 KP 回复中的 <!--GS ... --> 块，写入 GameState。

设计原则：
- 纯正则解析，零 LLM 调用，不增加延迟
- 解析失败静默跳过，不抛异常，不打断主流程
- 标记块从回复中移除，玩家不可见
- 所有修改记录 debug 日志
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_state import GameState

log = logging.getLogger(__name__)

# 匹配 <!--GS ... --> 块，支持多行
_GS_BLOCK_RE = re.compile(r"<!--GS\s*\n(.*?)\n\s*-->", re.DOTALL)

# 单行指令格式：key: value 或 key: value1,value2
_LINE_RE = re.compile(r"^\s*(\w+)\s*:\s*(.+?)\s*$")

# 态度中文 → 英文映射（与 game_state.ATTITUDE_LABELS 对应）
_ATTITUDE_CN_TO_EN: dict[str, str] = {
    "敌对": "hostile", "hostile": "hostile",
    "警惕": "wary", "wary": "wary",
    "中立": "neutral", "neutral": "neutral",
    "友善": "friendly", "friendly": "friendly",
    "忠诚": "loyal", "loyal": "loyal",
}


def parse_and_apply(gs: GameState, kp_answer: str) -> str:
    """从 KP 回复中提取 GS 标记块，写入 GameState，返回清洗后的回复。

    Args:
        gs: 当前游戏状态（会被原地修改）
        kp_answer: KP 的原始回复（可能包含 <!--GS 块）

    Returns:
        移除 GS 块后的干净回复文本
    """
    matches = list(_GS_BLOCK_RE.finditer(kp_answer))
    if not matches:
        return kp_answer

    cleaned = kp_answer
    total_applied = 0

    for match in matches:
        block_text = match.group(1)
        applied = _apply_block(gs, block_text)
        total_applied += applied
        # 移除整个块（含前后空白换行）
        cleaned = cleaned.replace(match.group(0), "")

    # 清理多余空行：连续三个以上换行 → 两个换行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    if total_applied:
        log.info("GS 标记：应用了 %d 条状态变更", total_applied)
    else:
        log.debug("GS 标记块存在但无有效指令（可能格式错误）")

    return cleaned


def _apply_block(gs: GameState, block_text: str) -> int:
    """解析一个 GS 块的全部指令行，应用到 GameState，返回成功应用数。"""
    applied = 0
    for raw_line in block_text.strip().split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        m = _LINE_RE.match(line)
        if not m:
            log.debug("GS 行格式无效: %r", line)
            continue

        key = m.group(1)
        value = m.group(2).strip()

        try:
            handled = _apply_line(gs, key, value)
            if handled:
                applied += 1
            else:
                log.debug("GS 未知指令: %s", key)
        except Exception:
            log.exception("GS 指令执行异常: %s = %r", key, value)
            # 不中断，继续处理后续行

    return applied


def _apply_line(gs: GameState, key: str, value: str) -> bool:
    """单条指令分发。返回 True 表示成功处理。"""

    # ── 线索 ──
    if key == "add_clue":
        title = value.strip().strip('"')
        if title and not any(q.title == title for q in gs.quests):
            from .game_state import Quest
            gs.quests.append(Quest(title=title, status="open"))
            log.debug("GS +线索: %s", title)
        return True

    # ── NPC ──
    elif key == "npc_new":
        return _handle_npc_new(gs, value)
    elif key == "npc_update":
        return _handle_npc_update(gs, value)
    elif key == "npc_attitude":
        return _handle_npc_attitude(gs, value)

    # ── 任务 ──
    elif key == "quest_new":
        from .game_state import Quest
        title = value.strip().strip('"')
        if title and not any(q.title == title for q in gs.quests):
            gs.quests.append(Quest(title=title, status="open"))
            log.debug("GS +任务: %s", title)
        return True
    elif key == "quest_resolve":
        return _handle_quest_resolve(gs, value)
    elif key == "quest_fail":
        return _handle_quest_status(gs, value, "failed")

    # ── 物品 ──
    elif key == "item_add":
        return _handle_item_add(gs, value)
    elif key == "item_remove":
        return _handle_item_remove(gs, value)

    # ── 角色状态 ──
    elif key == "hp_change":
        return _handle_hp_change(gs, value)
    elif key == "san_change":
        return _handle_san_change(gs, value)
    elif key == "condition_add":
        return _handle_condition_add(gs, value)
    elif key == "condition_remove":
        return _handle_condition_remove(gs, value)

    # ── 场景 ──
    elif key == "set_location":
        gs.location = value.strip().strip('"')
        log.debug("GS 场景: %s", gs.location)
        return True

    return False


# ── 辅助函数 ────────────────────────────────────────────


def _split_csv(value: str) -> list[str]:
    """解析逗号分隔的值，去除空白。"""
    return [v.strip().strip('"').strip("'") for v in value.split(",") if v.strip()]


def _handle_npc_new(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if not parts or not parts[0]:
        return False
    name = parts[0]
    if gs.find_npc(name):
        log.debug("GS NPC 已存在，跳过新建: %s", name)
        return False

    from .game_state import Npc
    npc = Npc(name=name)
    for kwarg in parts[1:]:
        if "=" in kwarg:
            k, v = kwarg.split("=", 1)
            k, v = k.strip(), v.strip().strip('"')
            if k in ("位置", "location"):
                npc.location = v
            elif k in ("态度", "attitude"):
                npc.attitude = _ATTITUDE_CN_TO_EN.get(v, v)
            elif k in ("描述", "description"):
                npc.description = v
    gs.npcs.append(npc)
    log.debug("GS +NPC: %s", name)
    return True


def _handle_npc_update(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if not parts or not parts[0]:
        return False
    name = parts[0]
    npc = gs.find_npc(name)
    if not npc:
        log.debug("GS NPC 未找到: %s", name)
        return False

    for kwarg in parts[1:]:
        if "=" in kwarg:
            k, v = kwarg.split("=", 1)
            k, v = k.strip(), v.strip().strip('"')
            if k in ("位置", "location"):
                npc.location = v
            elif k in ("态度", "attitude"):
                npc.attitude = _ATTITUDE_CN_TO_EN.get(v, v)
            elif k in ("描述", "description"):
                npc.description = v
    log.debug("GS NPC更新: %s", name)
    return True


def _handle_npc_attitude(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    name, attitude = parts[0], parts[1]
    attitude = _ATTITUDE_CN_TO_EN.get(attitude, attitude)
    result = gs.step_attitude(name, attitude)
    if result:
        log.debug("GS NPC态度: %s -> %s", name, result)
    return result is not None


def _handle_quest_resolve(gs: GameState, value: str) -> bool:
    return _handle_quest_status(gs, value, "resolved")


def _handle_quest_status(gs: GameState, value: str, status: str) -> bool:
    title = value.strip().strip('"')
    for q in gs.quests:
        if q.title == title:
            q.status = status
            log.debug("GS 任务%s: %s", status, title)
            return True
    # 模糊匹配
    for q in gs.quests:
        if title in q.title or q.title in title:
            q.status = status
            log.debug("GS 任务%s(模糊): %s", status, q.title)
            return True
    log.debug("GS 任务未找到: %s", title)
    return False


def _handle_item_add(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    char_name, item = parts[0], parts[1]
    inv = gs.find_investigator(char_name)
    if not inv:
        log.debug("GS 调查员未找到: %s", char_name)
        return False
    if item not in inv.inventory:
        inv.inventory.append(item)
        log.debug("GS +物品: %s -> %s", char_name, item)
    return True


def _handle_item_remove(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    char_name, item = parts[0], parts[1]
    inv = gs.find_investigator(char_name)
    if not inv:
        return False
    if item in inv.inventory:
        inv.inventory.remove(item)
        log.debug("GS -物品: %s -> %s", char_name, item)
    return True


def _handle_hp_change(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    name, amount_str = parts[0], parts[1]
    inv = gs.find_investigator(name)
    if not inv:
        return False
    try:
        amount = int(amount_str)
    except ValueError:
        return False
    if amount < 0:
        inv.take_damage(-amount)
    else:
        inv.heal(amount)
    log.debug("GS HP: %s %+d (当前 %d/%d)", name, amount, inv.hp, inv.max_hp)
    return True


def _handle_san_change(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    name, amount_str = parts[0], parts[1]
    inv = gs.find_investigator(name)
    if not inv:
        return False
    try:
        amount = int(amount_str)
    except ValueError:
        return False
    if amount < 0:
        inv.lose_san(-amount)
    else:
        inv.san = min(inv.max_san, inv.san + amount)
    log.debug("GS SAN: %s %+d (当前 %d/%d)", name, amount, inv.san, inv.max_san)
    return True


def _handle_condition_add(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    name, condition = parts[0], parts[1]
    inv = gs.find_investigator(name)
    if not inv:
        return False
    if condition not in inv.conditions:
        inv.conditions.append(condition)
        log.debug("GS +状态: %s -> %s", name, condition)
    return True


def _handle_condition_remove(gs: GameState, value: str) -> bool:
    parts = _split_csv(value)
    if len(parts) < 2:
        return False
    name, condition = parts[0], parts[1]
    inv = gs.find_investigator(name)
    if not inv:
        return False
    # 模糊匹配
    removed = [c for c in inv.conditions if condition in c]
    for c in removed:
        inv.conditions.remove(c)
        log.debug("GS -状态: %s -> %s", name, c)
    return bool(removed)
