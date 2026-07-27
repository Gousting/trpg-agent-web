"""TRPG Agent Web — FastAPI + SSE 流式 COC 跑团。

启动:
    uv run python -m trpg_agent_web.web_server
    # 浏览器打开 http://localhost:8766
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.memory.game_state import Investigator
from trpg_agent.mapgen import DungeonMap
from trpg_agent.scene_matcher import SceneMatcher

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════

INVESTIGATORS: list[dict] = [
    {"name": "陈明", "hp": 12, "max_hp": 12, "san": 60, "max_san": 60, "luck": 50,
     "skills": {"侦查": 60, "图书馆": 50, "说服": 40, "格斗": 50, "潜行": 45, "手枪": 45},
     "inventory": ["手电筒", "警徽", ".38左轮"],
     "portrait": "a23d09217aea7606a1e21ba59e302543.jpg", "color": "#4a90d9"},
    {"name": "林晓", "hp": 10, "max_hp": 10, "san": 70, "max_san": 70, "luck": 45,
     "skills": {"医学": 65, "急救": 60, "心理学": 50, "神秘学": 30, "侦查": 35},
     "inventory": ["急救包", "笔记本", "相机"],
     "portrait": "f9c158d8dd94506a897ea1bc5f4f401d.jpg", "color": "#50c878"},
    {"name": "王刚", "hp": 15, "max_hp": 15, "san": 40, "max_san": 40, "luck": 55,
     "skills": {"格斗": 70, "投掷": 50, "攀爬": 55, "恐吓": 45},
     "inventory": ["棒球棍", "打火机", "香烟"],
     "portrait": "ba60ae0b2c814d1fc7291f97baf0e57a.jpg", "color": "#e07050"},
]

OPENING = "1928年深秋，你们收到匿名信，来到阿卡姆郊外的废弃疗养院。推开吱呀作响的大门，你们踏入了这座被诅咒的建筑。"

# ═══════════════════════════════════════════════════════
# Web 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(title="COC TRPG 跑团")
STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SCENES_DIR = DATA_DIR / "scenes" / "Sceneimage"
BGM_DIR = DATA_DIR / "bgm"
CHARS_DIR = DATA_DIR / "characters" / "Userimage"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/images/scenes", StaticFiles(directory=str(SCENES_DIR)), name="scenes")
app.mount("/images/characters", StaticFiles(directory=str(CHARS_DIR)), name="characters")
app.mount("/audio/bgm", StaticFiles(directory=str(BGM_DIR)), name="bgm")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ── 工具函数 ────────────────────────────────────


def _scene_matcher() -> SceneMatcher:
    """惰性场景匹配器。"""
    sm = SceneMatcher()
    sm.load()
    return sm


def _state_snapshot(session: Session) -> dict:
    """调查员状态快照。"""
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


async def _chat_stream(client: OllamaClient, system: str, user_msg: str,
                       temperature: float = 0.8, max_tokens: int = 2000):
    """流式聊天，yield token。"""
    try:
        async for token in client.chat_stream(
            system, [{"role": "user", "content": user_msg}],
            options={"temperature": temperature, "num_predict": max_tokens},
        ):
            yield token
    except Exception as e:
        yield f"\n[错误] {e}"


def _match_scene(text: str) -> dict | None:
    """从叙述文本匹配场景图。"""
    try:
        sm = _scene_matcher()
        matches = sm.match(text, top_k=1)
        if matches:
            m = matches[0]
            return {"image": m.filename, "location": m.location, "mood": m.mood}
    except Exception:
        pass
    return None


# ── SSE 事件流 ──────────────────────────────────


async def event_stream(host: str, kp_model: str, player_model: str,
                       turns: int, seed: int | None, mode: str):
    """SSE 事件流 — 完整的游戏循环。"""
    yield _sse("status", {"text": "生成地图..."})

    # ── 地图 ──────────────────────────────────
    dmap = DungeonMap(STATIC_DIR / "maps")
    dmap.generate(seed=seed, num_rooms=10)
    dmap.render()
    current_room = dmap.current_room
    rc = dmap.room_context()
    yield _sse("map", {
        "map": dmap.to_dict(), "grid": dmap.grid,
        "image": dmap.relative_path,
    })

    # ── 模型检查 ──────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=5) as cl:
            resp = await cl.get(f"{host}/api/tags")
            available = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        available = []

    if available and kp_model not in available:
        yield _sse("error", {"text": f"KP模型 {kp_model} 不可用"})
        return

    kp_client = OllamaClient(host, kp_model, num_ctx=8192, timeout=180)

    # ── Session ───────────────────────────────
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
    session.state.location = current_room.name if current_room else ""

    yield _sse("init", {
        "investigators": INVESTIGATORS,
        "kp_model": kp_model, "player_model": player_model,
        "opening": OPENING, "room": rc, "mode": mode,
    })

    # ── KP 开场（流式）────────────────────────
    system_prompt = session.build_system_prompt()
    kp_user_msg = (
        f"场景：{OPENING}\n"
        f"当前房间：{rc['name']} — {rc['desc']}\n"
        f"调查员：{', '.join(i['name'] for i in INVESTIGATORS)}\n"
        "请描述开场场景，营造恐怖氛围。"
    )
    yield _sse("kp_stream_start", {})
    opening_text = ""
    async for token in _chat_stream(kp_client, system_prompt, kp_user_msg,
                                    temperature=0.8, max_tokens=2500):
        opening_text += token
        yield _sse("kp_token", {"text": token})
    if not opening_text:
        opening_text = f"你们站在{current_room.name}中。{current_room.description}"
    session.record_turn("(游戏开始)", opening_text)

    # 场景匹配
    scene_info = _match_scene(opening_text)
    yield _sse("kp_stream_end", {
        "state": _state_snapshot(session),
        "scene": scene_info,
    })
    await asyncio.sleep(0.5)

    # ── 游戏循环 ──────────────────────────────
    last_narration = opening_text
    player_order = [inv["name"] for inv in INVESTIGATORS]

    for turn in range(turns):
        speaker = player_order[turn % len(player_order)]
        inv_data = next(inv for inv in INVESTIGATORS if inv["name"] == speaker)
        inv_state = session.state.find_investigator(speaker)
        rc = dmap.room_context()

        # ── 玩家行动 ───────────────────────────
        if mode == "ai":
            action = await _ai_player_turn(
                host, player_model, session, inv_data, inv_state, rc,
                last_narration, speaker,
            )
            # yield player turn via SSE
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            for chunk in _split_for_stream(action):
                yield _sse("player_token", {"text": chunk, "speaker": speaker})
                await asyncio.sleep(0.02)
            yield _sse("player_stream_end", {"speaker": speaker})
            await asyncio.sleep(0.3)
        else:
            # 人类模式：等待前端发来玩家输入
            yield _sse("await_player", {
                "speaker": speaker, "color": inv_data["color"],
                "room": rc,
            })
            return  # 人类模式一轮后交给前端轮询

        # ── 房间物品拾取 ───────────────────────
        items_picked = _handle_pickup(dmap, inv_state, action)

        # ── 检定 ───────────────────────────────
        dice_context, dice_result = "", {}
        try:
            dice_context, roll_req = await session.classify_and_resolve(
                kp_client, action)
            if roll_req:
                dice_result = {"skill": roll_req.skill, "difficulty": roll_req.difficulty}
        except Exception:
            pass
        if dice_context:
            yield _sse("dice_roll", {
                "speaker": speaker, "text": dice_context,
                "skill": dice_result.get("skill", ""),
            })
            await asyncio.sleep(0.5)

        # ── KP 叙述（流式）────────────────────
        system_prompt = session.build_system_prompt()
        context_parts = []
        if dice_context:
            context_parts.append(f"[检定] {dice_context}")
        if items_picked:
            context_parts.append(f"[获得物品] {', '.join(items_picked)}")
        context_parts.append(f"[{speaker}] {action}")
        kp_user = "\n\n".join(context_parts) + "\n\n请叙述结果："

        yield _sse("kp_stream_start", {})
        narration = ""
        async for token in _chat_stream(kp_client, system_prompt, kp_user,
                                        temperature=0.8, max_tokens=2500):
            narration += token
            yield _sse("kp_token", {"text": token})
        if not narration:
            narration = "（KP 沉思……）"

        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration

        # 场景匹配
        scene_info = _match_scene(narration)

        # 房间移动检测
        room_change = _detect_move(dmap, action)

        yield _sse("kp_stream_end", {
            "state": _state_snapshot(session),
            "scene": scene_info,
            "room_change": room_change,
        })

        if room_change:
            session.state.location = room_change.get("room_name", "")
            yield _sse("room_change", room_change)
            await asyncio.sleep(0.5)

        await asyncio.sleep(0.3)

    yield _sse("done", {"summary": session.state.scene_summary()})


async def _ai_player_turn(host: str, player_model: str, session: Session,
                          inv_data: dict, inv_state, rc: dict,
                          last_narration: str, speaker: str) -> str:
    """AI 扮演玩家生成行动文本。"""
    skills_str = json.dumps(inv_state.skills, ensure_ascii=False)
    items_str = ", ".join(inv_state.inventory) if inv_state.inventory else "无"
    player_system = (
        f"你是 {inv_data['name']}，克苏鲁的呼唤调查员。\n"
        f"HP:{inv_state.hp}/{inv_state.max_hp} SAN:{inv_state.san}/{inv_state.max_san}\n"
        f"技能：{skills_str}  物品：{items_str}\n\n"
        f"当前房间：{rc['name']} — {rc['desc']}\n"
        f"出口：{rc['exits']}  物品：{rc['items']}  威胁：{rc['threats']}\n\n"
        "用第一人称描述行动，1-2句话。探索、调查、或应对威胁。不要替其他调查员说话。"
    )
    player_msg = f"主持人叙述：{last_narration[:600]}\n\n{inv_data['name']}的行动："
    player_client = OllamaClient(host, player_model, num_ctx=4096, timeout=120)
    try:
        action = await player_client.chat(player_system,
                                          [{"role": "user", "content": player_msg}],
                                          options={"temperature": 0.9, "num_predict": 2000})
        await player_client.aclose()
        return action.strip() if action.strip() else f"（{speaker} 谨慎地观察四周）"
    except Exception:
        await player_client.aclose()
        return f"（{speaker} 谨慎地观察四周）"


def _split_for_stream(text: str, chunk_size: int = 3) -> list[str]:
    """把文本切成小块用于流式输出。"""
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]


def _handle_pickup(dmap: DungeonMap, inv_state, action: str) -> list[str]:
    """处理房间物品拾取。"""
    items_picked: list[str] = []
    room = dmap.current_room
    if not room:
        return items_picked
    pickup_keywords = ["拿", "捡", "收集", "取", "拿走", "拾起"]
    wants_pickup = any(kw in action for kw in pickup_keywords)
    if wants_pickup:
        for item in list(room.items):
            if item in action:
                inv_state.inventory.append(item)
                room.items.remove(item)
                items_picked.append(item)
    return items_picked


def _detect_move(dmap: DungeonMap, action: str) -> dict | None:
    """检测房间移动。"""
    room = dmap.current_room
    if not room:
        return None
    for conn_id in room.connections:
        neighbor = dmap.get_room(conn_id)
        if not neighbor:
            continue
        name_short = neighbor.name.split("(")[0].strip()
        if len(name_short) >= 2 and name_short in action:
            if not any(neg in action for neg in ["不去", "离开", "返回"]):
                dmap.move_to(conn_id)
                current = dmap.current_room
                rc = dmap.room_context()
                return {
                    "room_id": conn_id,
                    "room_name": current.name if current else "",
                    "room_desc": current.description if current else "",
                    "items": current.items if current else [],
                    "map": dmap.to_dict(),
                    "grid": dmap.grid,
                    "image": dmap.relative_path,
                    "room": rc,
                }
    return None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 路由 ─────────────────────────────────────


class PlayerAction(BaseModel):
    text: str
    speaker: str


class GameConfig(BaseModel):
    host: str = "http://localhost:11434"
    kp: str = "gemma4:12b"
    player: str = "ornith:9b"
    turns: int = 12
    seed: str = ""
    mode: str = "ai"  # "ai" | "human"


@app.get("/api/stream")
async def stream(
    host: str = "http://localhost:11434",
    kp: str = "gemma4:12b",
    player: str = "ornith:9b",
    turns: int = 12,
    seed: str = "",
    mode: str = "ai",
):
    try:
        seed_val = int(seed) if seed else None
    except ValueError:
        seed_val = None
    return StreamingResponse(
        event_stream(host, kp, player, turns, seed_val, mode),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 入口 ─────────────────────────────────────


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(description="TRPG Agent Web Server")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
