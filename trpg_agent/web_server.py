"""TRPG Roguelike 跑团 Web 界面 — FastAPI + SSE + 程序化地图。

启动:
    uv run python -m trpg_agent.web_server
    # 浏览器打开 http://localhost:8766
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.memory.game_state import Investigator
from trpg_agent.mapgen import generate_map, map_to_dict, GameMap, Room

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════

INVESTIGATORS = [
    {"name": "陈明", "hp": 12, "max_hp": 12, "san": 60, "max_san": 60, "luck": 50,
     "skills": {"侦查": 60, "图书馆": 50, "说服": 40, "格斗": 50, "潜行": 45, "手枪": 45},
     "inventory": ["手电筒", "警徽", ".38左轮"],
     "personality": "退役刑警，沉默寡言，先侦察再行动。", "color": "#4a90d9"},
    {"name": "林晓", "hp": 10, "max_hp": 10, "san": 70, "max_san": 70, "luck": 45,
     "skills": {"医学": 65, "急救": 60, "心理学": 50, "神秘学": 30, "侦查": 35},
     "inventory": ["急救包", "笔记本", "相机"],
     "personality": "年轻法医，好奇心旺盛，紧张时碎碎念。", "color": "#50c878"},
    {"name": "王刚", "hp": 15, "max_hp": 15, "san": 40, "max_san": 40, "luck": 55,
     "skills": {"格斗": 70, "投掷": 50, "攀爬": 55, "恐吓": 45},
     "inventory": ["棒球棍", "打火机", "香烟"],
     "personality": "码头工人，遇事先动手再说。", "color": "#e07050"},
]

OPENING = "1928年深秋，你们收到匿名信，来到阿卡姆郊外的废弃疗养院。推开吱呀作响的大门，你们踏入了这座被诅咒的建筑。"

PLAYER_PROMPT = """你是 {name}，克苏鲁的呼唤调查员。
性格：{personality}
HP:{hp}/{max_hp} SAN:{san}/{max_san}  技能：{skills}  物品：{items}

当前房间：{room_name} — {room_desc}
可用出口：{exits}
房间物品：{room_items}
威胁：{room_threats}

用第一人称描述行动，1-2句话。可以是探索、拿物品、移动到其他房间、或应对威胁。"""

KP_PROMPT = """你是克苏鲁的呼唤主持人。用中文叙述。
{scene}
当前场景：{room}
规则：不替调查员说话；检定结果已给出，按结果叙述；保持恐怖氛围；描述环境变化。"""

# 移动关键词
MOVE_PATTERNS = re.compile(r'(移动|走向|进入|前往|去|到|推开.*门|上楼|下楼|返回)(.+)')


# ═══════════════════════════════════════════════════════
# Web 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(title="TRPG Roguelike 跑团")
STATIC = Path(__file__).resolve().parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


async def chat_retry(client: OllamaClient, system: str, user_msg: str,
                     temperature: float = 0.8, max_tokens: int = 2000) -> str:
    for _ in range(3):
        try:
            resp = await client.chat(
                system, [{"role": "user", "content": user_msg}],
                options={"temperature": temperature, "num_predict": max_tokens},
            )
            if resp and resp.strip():
                return resp.strip()
            max_tokens = int(max_tokens * 1.5)
            await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(2)
    return ""


def state_snapshot(session: Session) -> dict:
    return {
        inv.name: {
            "hp": inv.hp, "max_hp": inv.max_hp,
            "san": inv.san, "max_san": inv.max_san,
            "luck": inv.luck,
            "conditions": list(inv.conditions),
            "inventory": list(inv.inventory),
        }
        for inv in session.state.investigators
    }


def _detect_move(action: str, current_room: Room) -> str | None:
    """从玩家行动中检测是否要移动到其他房间。返回目标房间 ID 或 None。"""
    # 精确匹配出口名称
    for conn_id in current_room.connections:
        # 通过 room id 反查房间名
        pass  # 需要在上下文中查找
    # 简化：如果行动包含移动关键词且提到了出口方向
    m = MOVE_PATTERNS.search(action)
    return None  # 先由 LLM 决定移动，不做自动检测


def _room_context(room: Room, gmap: GameMap) -> dict:
    """构建房间上下文给 prompt。"""
    exits = []
    for cid in room.connections:
        neighbor = gmap.rooms[cid]
        direction = "已探索" if neighbor.visited else "未探索"
        exits.append(f"{neighbor.name}({direction})")

    return {
        "name": room.name,
        "desc": room.description,
        "exits": ", ".join(exits) if exits else "无",
        "items": ", ".join(room.items) if room.items else "无",
        "threats": ", ".join(t[0] for t in room.threats) if room.threats else "无",
    }


async def event_stream(host: str, kp_model: str, player_model: str, turns: int, seed: int | None):
    """SSE 事件流。"""
    yield f"data: {json.dumps({'type': 'status', 'text': '生成地图...'}, ensure_ascii=False)}\n\n"

    # ── 生成地图 ──────────────────────────────────
    gmap = generate_map(seed=seed, num_rooms=10)
    current_room = gmap.rooms[gmap.current_room_id]
    yield f"data: {json.dumps({'type': 'map', 'map': map_to_dict(gmap)}, ensure_ascii=False)}\n\n"

    # ── 初始化 ──────────────────────────────────
    async with httpx.AsyncClient(timeout=5) as cl:
        resp = await cl.get(f"{host}/api/tags")
        available = [m["name"] for m in resp.json().get("models", [])]

    kp_client = OllamaClient(host, kp_model, num_ctx=8192, timeout=180)

    sid = f"web_{datetime.now().strftime('%m%d_%H%M%S')}"
    old_dir = Path("data/sessions") / sid
    if old_dir.exists():
        shutil.rmtree(old_dir)
    session = Session(sid, auto_save_interval=0, max_context=8192)

    for inv_data in INVESTIGATORS:
        inv = Investigator(
            name=inv_data["name"], hp=inv_data["hp"], max_hp=inv_data["max_hp"],
            san=inv_data["san"], max_san=inv_data["max_san"], luck=inv_data["luck"],
            skills=inv_data["skills"], inventory=list(inv_data.get("inventory", [])),
        )
        session.state.investigators.append(inv)
    session.state.location = current_room.name

    yield f"data: {json.dumps({'type': 'init', 'investigators': INVESTIGATORS, 'kp_model': kp_model, 'player_model': player_model, 'opening': OPENING, 'room': _room_context(current_room, gmap)}, ensure_ascii=False)}\n\n"

    # ── KP 开场 ──────────────────────────────────
    rc = _room_context(current_room, gmap)
    kp_system = KP_PROMPT.format(
        scene=f"场景：{OPENING}",
        room=f"{rc['name']} — {rc['desc']}\n出口：{rc['exits']}\n物品：{rc['items']}",
    )
    opening = await chat_retry(kp_client, kp_system,
                               f"调查员：{', '.join(i['name'] for i in INVESTIGATORS)}\n请描述开场场景。",
                               temperature=0.8, max_tokens=2500)
    if not opening:
        opening = f"你们站在{current_room.name}中。{current_room.description}"
    session.record_turn("(游戏开始)", opening)
    yield f"data: {json.dumps({'type': 'kp_narration', 'speaker': 'KP', 'text': opening, 'state': state_snapshot(session)}, ensure_ascii=False)}\n\n"
    await asyncio.sleep(1)

    # ── 游戏循环 ────────────────────────────────
    last_narration = opening
    player_order = [inv["name"] for inv in INVESTIGATORS]

    for turn in range(turns):
        speaker = player_order[turn % len(player_order)]
        inv_data = next(inv for inv in INVESTIGATORS if inv["name"] == speaker)
        inv_state = session.state.find_investigator(speaker)

        rc = _room_context(current_room, gmap)

        # 玩家行动
        player_system = PLAYER_PROMPT.format(
            name=inv_data["name"], personality=inv_data["personality"],
            hp=inv_state.hp, max_hp=inv_state.max_hp,
            san=inv_state.san, max_san=inv_state.max_san,
            skills=json.dumps(inv_state.skills, ensure_ascii=False),
            items=", ".join(inv_state.inventory) if inv_state.inventory else "无",
            room_name=rc["name"], room_desc=rc["desc"],
            exits=rc["exits"], room_items=rc["items"],
            room_threats=rc["threats"],
        )
        action = await chat_retry(
            OllamaClient(host, player_model, num_ctx=4096, timeout=120),
            player_system,
            f"主持人叙述：{last_narration[:600]}\n\n{inv_data['name']}的行动：",
            temperature=0.9, max_tokens=2000,
        )
        if not action:
            action = f"（{speaker} 谨慎地观察四周）"

        yield f"data: {json.dumps({'type': 'player_action', 'speaker': speaker, 'text': action, 'color': inv_data['color']}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.3)

        # ── 处理房间物品拾取 ──────────────────────
        items_picked = []
        for item in list(current_room.items):
            if item.lower() in action.lower() or "拿" in action or "捡" in action or "收集" in action:
                inv_state.inventory.append(item)
                current_room.items.remove(item)
                items_picked.append(item)

        if items_picked:
            yield f"data: {json.dumps({'type': 'item_pickup', 'speaker': speaker, 'items': items_picked}, ensure_ascii=False)}\n\n"

        # ── 处理威胁 ──────────────────────────────
        threat_text = ""
        if current_room.threats and not current_room.cleared and "战斗" not in threat_text:
            threat_name, threat_check = current_room.threats[0]
            if any(kw in action for kw in ["攻击", "打", "开火", "开枪"]):
                threat_text = f"⚔️ {threat_name}: {threat_check}"
                current_room.cleared = True
                # 模拟战斗伤害
                import random
                dmg = random.randint(0, 3)
                if dmg > 0:
                    inv_state.take_damage(dmg)
                    yield f"data: {json.dumps({'type': 'damage', 'speaker': speaker, 'amount': dmg, 'source': threat_name}, ensure_ascii=False)}\n\n"

        if threat_text:
            yield f"data: {json.dumps({'type': 'dice_roll', 'speaker': speaker, 'text': threat_text}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

        # ── 检定 ──────────────────────────────────
        dice_context = ""
        try:
            dice_context, _ = await session.classify_and_resolve(kp_client, action)
        except Exception:
            pass
        if dice_context:
            yield f"data: {json.dumps({'type': 'dice_roll', 'speaker': speaker, 'text': dice_context}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

        # ── KP 叙述 ───────────────────────────────
        scene = session.state.scene_summary()
        kp_system = KP_PROMPT.format(
            scene=scene,
            room=f"{rc['name']} — {rc['desc']}\n出口：{rc['exits']}",
        )

        context_parts = []
        if dice_context:
            context_parts.append(f"[检定] {dice_context}")
        if threat_text:
            context_parts.append(f"[威胁] {threat_text}")
        if items_picked:
            context_parts.append(f"[获得物品] {', '.join(items_picked)}")
        context_parts.append(f"[{speaker}] {action}")
        kp_user = "\n\n".join(context_parts) + "\n\n请叙述结果："

        narration = await chat_retry(kp_client, kp_system, kp_user, temperature=0.8, max_tokens=2500)
        if not narration:
            narration = "（KP 沉思……）"

        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration

        # ── 检测房间移动 ──────────────────────────
        new_room_id = None
        for conn_id in current_room.connections:
            neighbor = gmap.rooms[conn_id]
            if neighbor.name.replace("(", "").split("(")[0] in action:
                new_room_id = conn_id
                break

        if new_room_id:
            current_room = gmap.rooms[new_room_id]
            gmap.current_room_id = new_room_id
            if not current_room.visited:
                current_room.visited = True
            session.state.location = current_room.name
            yield f"data: {json.dumps({'type': 'room_change', 'room_id': new_room_id, 'room_name': current_room.name, 'room_desc': current_room.description, 'items': current_room.items, 'map': map_to_dict(gmap)}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

        # 推送状态
        yield f"data: {json.dumps({'type': 'kp_narration', 'speaker': 'KP', 'text': narration, 'state': state_snapshot(session)}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.3)

    yield f"data: {json.dumps({'type': 'done', 'summary': session.loaded_state_summary()}, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(
    host: str = "http://192.168.0.108:11434",
    kp: str = "gemma4:12b",
    player: str = "ornith:9b",
    turns: int = 12,
    seed: str = "",
):
    seed_val = int(seed) if seed else None
    return StreamingResponse(
        event_stream(host, kp, player, turns, seed_val),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main():
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
