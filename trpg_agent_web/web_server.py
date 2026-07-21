"""TRPG Roguelike 跑团 Web 界面 — FastAPI + SSE + 程序化地图。

启动:
    uv run python -m trpg_agent_web.web_server
    # 浏览器打开 http://localhost:8766
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.memory.game_state import Investigator
from trpg_agent.mapgen import DungeonMap

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


# ═══════════════════════════════════════════════════════
# Web 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(title="TRPG Roguelike 跑团")
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


class MapData(BaseModel):
    image: str  # base64 PNG
    seed: str = ""


@app.post("/api/save-map")
async def save_map(data: MapData):
    """保存 One Page Dungeon 生成的 PNG 地图。"""
    import base64
    maps_dir = STATIC_DIR / "maps"
    maps_dir.mkdir(exist_ok=True)
    fname = f"dungeon_{data.seed or 'latest'}.png"
    img_bytes = base64.b64decode(data.image)
    (maps_dir / fname).write_bytes(img_bytes)
    return JSONResponse({"ok": True, "path": f"/static/maps/{fname}", "size": len(img_bytes)})


async def chat_stream_once(client: OllamaClient, system: str, user_msg: str,
                           temperature: float = 0.8, max_tokens: int = 2000):
    """流式聊天，yield token。失败时 yield 空。"""
    try:
        async for token in client.chat_stream(
            system, [{"role": "user", "content": user_msg}],
            options={"temperature": temperature, "num_predict": max_tokens},
        ):
            yield token
    except Exception:
        pass


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


async def event_stream(host: str, kp_model: str, player_model: str, turns: int, seed: int | None):
    """SSE 事件流。"""
    # ── 地图组件 ──────────────────────────────────
    dmap = DungeonMap(STATIC_DIR / "maps")
    yield f"data: {json.dumps({'type': 'status', 'text': '生成地图...'}, ensure_ascii=False)}\n\n"
    dmap.generate(seed=seed, num_rooms=10)
    dmap.render()
    current_room = dmap.current_room
    yield f"data: {json.dumps({'type': 'map', 'map': dmap.to_dict(), 'grid': dmap.grid, 'image': dmap.relative_path}, ensure_ascii=False)}\n\n"

    # ── 模型初始化 ──────────────────────────────
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

    yield f"data: {json.dumps({'type': 'init', 'investigators': INVESTIGATORS, 'kp_model': kp_model, 'player_model': player_model, 'opening': OPENING, 'room': dmap.room_context()}, ensure_ascii=False)}\n\n"

    # ── KP 开场（流式）──────────────────────────
    rc = dmap.room_context()
    kp_system = KP_PROMPT.format(
        scene=f"场景：{OPENING}",
        room=f"{rc['name']} — {rc['desc']}\n出口：{rc['exits']}\n物品：{rc['items']}",
    )
    kp_user_msg = f"调查员：{', '.join(i['name'] for i in INVESTIGATORS)}\n请描述开场场景。"
    yield f"data: {json.dumps({'type': 'kp_stream_start'}, ensure_ascii=False)}\n\n"
    opening = ""
    async for token in chat_stream_once(kp_client, kp_system, kp_user_msg, temperature=0.8, max_tokens=2500):
        opening += token
        yield f"data: {json.dumps({'type': 'kp_token', 'text': token}, ensure_ascii=False)}\n\n"
    if not opening:
        opening = f"你们站在{current_room.name}中。{current_room.description}"
    session.record_turn("(游戏开始)", opening)
    yield f"data: {json.dumps({'type': 'kp_stream_end', 'state': state_snapshot(session)}, ensure_ascii=False)}\n\n"
    await asyncio.sleep(0.5)

    # ── 游戏循环 ────────────────────────────────
    last_narration = opening
    player_order = [inv["name"] for inv in INVESTIGATORS]

    for turn in range(turns):
        speaker = player_order[turn % len(player_order)]
        inv_data = next(inv for inv in INVESTIGATORS if inv["name"] == speaker)
        inv_state = session.state.find_investigator(speaker)

        rc = dmap.room_context()

        # 玩家行动（流式）
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
        player_client = OllamaClient(host, player_model, num_ctx=4096, timeout=120)
        player_msg = f"主持人叙述：{last_narration[:600]}\n\n{inv_data['name']}的行动："
        yield f"data: {json.dumps({'type': 'player_stream_start', 'speaker': speaker, 'color': inv_data['color']}, ensure_ascii=False)}\n\n"
        action = ""
        async for token in chat_stream_once(player_client, player_system, player_msg, temperature=0.9, max_tokens=2000):
            action += token
            yield f"data: {json.dumps({'type': 'player_token', 'text': token, 'speaker': speaker}, ensure_ascii=False)}\n\n"
        if not action:
            action = f"（{speaker} 谨慎地观察四周）"
        yield f"data: {json.dumps({'type': 'player_stream_end', 'speaker': speaker}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.2)

        # ── 处理房间物品拾取 ──────────────────────
        items_picked = []
        room = dmap.current_room
        if room:
            for item in list(room.items):
                if item.lower() in action.lower() or any(kw in action for kw in ["拿", "捡", "收集"]):
                    inv_state.inventory.append(item)
                    room.items.remove(item)
                    items_picked.append(item)

        if items_picked:
            yield f"data: {json.dumps({'type': 'item_pickup', 'speaker': speaker, 'items': items_picked}, ensure_ascii=False)}\n\n"

        # ── 处理威胁 ──────────────────────────────
        threat_text = ""
        if room and room.threats and not room.cleared:
            threat_name, threat_check = room.threats[0]
            if any(kw in action for kw in ["攻击", "打", "开火", "开枪"]):
                threat_text = f"⚔️ {threat_name}: {threat_check}"
                room.cleared = True
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

        # ── KP 叙述（流式）───────────────────────
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

        yield f"data: {json.dumps({'type': 'kp_stream_start'}, ensure_ascii=False)}\n\n"
        narration = ""
        async for token in chat_stream_once(kp_client, kp_system, kp_user, temperature=0.8, max_tokens=2500):
            narration += token
            yield f"data: {json.dumps({'type': 'kp_token', 'text': token}, ensure_ascii=False)}\n\n"
        if not narration:
            narration = "（KP 沉思……）"

        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration

        # ── 检测房间移动 ──────────────────────────
        new_room_id = None
        if room:
            for conn_id in room.connections:
                neighbor = dmap.get_room(conn_id)
                if neighbor and neighbor.name.replace("(", "").split("(")[0] in action:
                    new_room_id = conn_id
                    break

        if new_room_id:
            dmap.move_to(new_room_id)
            current_room = dmap.current_room
            session.state.location = current_room.name if current_room else ""
            yield f"data: {json.dumps({'type': 'room_change', 'room_id': new_room_id, 'room_name': current_room.name if current_room else '', 'room_desc': current_room.description if current_room else '', 'items': current_room.items if current_room else [], 'map': dmap.to_dict(), 'grid': dmap.grid, 'image': dmap.relative_path}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

        # 推送回合结束（含状态）
        yield f"data: {json.dumps({'type': 'kp_stream_end', 'state': state_snapshot(session)}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.3)

    yield f"data: {json.dumps({'type': 'done', 'summary': session.loaded_state_summary()}, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(
    host: str = "http://192.168.0.107:11434",
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
