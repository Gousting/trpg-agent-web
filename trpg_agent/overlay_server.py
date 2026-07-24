"""OBS overlay server — WebSocket 推送 COC 直播数据到浏览器覆盖层."""
from __future__ import annotations

import asyncio
import json
import pathlib
from dataclasses import dataclass, field, asdict
from typing import Optional

import aiohttp
from aiohttp import web

OVERLAY_DIR = pathlib.Path(__file__).parent.parent / "docs"

# ── Data models ──

@dataclass
class SceneState:
    image: str = ""          # 场景图片文件名 (16:9)
    location: str = ""
    mood: str = ""           # 氛围标签

@dataclass
class NarrativeState:
    lines: list[str] = field(default_factory=list)
    current_index: int = -1

@dataclass
class DiceState:
    visible: bool = False
    value: int = 0
    skill: str = ""
    target: int = 0
    character: str = ""
    success: Optional[bool] = None

@dataclass
class CharacterState:
    name: str = ""
    role: str = ""
    hp: int = 0
    hp_max: int = 0
    san: int = 0
    san_max: int = 0
    luck: int = 0
    luck_max: int = 0
    status: str = ""
    active: bool = False

@dataclass
class DanmakuState:
    messages: list[dict] = field(default_factory=list)  # [{text, type}]

@dataclass
class VoteState:
    visible: bool = False
    prompt: str = ""
    options: list[dict] = field(default_factory=list)  # [{label, pct, leading}]

# ── Global state ──

scene = SceneState()
narrative = NarrativeState()
dice = DiceState()
characters: list[CharacterState] = []
danmaku = DanmakuState()
vote = VoteState()

connected_clients: set[web.WebSocketResponse] = set()


def build_state_message() -> dict:
    return {
        "type": "full_sync",
        "scene": asdict(scene),
        "narrative": asdict(narrative),
        "dice": asdict(dice),
        "characters": [asdict(c) for c in characters],
        "danmaku": asdict(danmaku),
        "vote": asdict(vote),
    }


async def broadcast(data: dict):
    msg = json.dumps(data)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_str(msg)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


# ── HTTP/WS handlers ──

async def index(request: web.Request) -> web.Response:
    return web.FileResponse(OVERLAY_DIR / "overlay_b.html")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    # Send full sync on connect
    await ws.send_str(json.dumps(build_state_message()))
    try:
        async for _ in ws:
            pass  # client can send pings, we respond
    finally:
        connected_clients.discard(ws)
    return ws


# ── REST API for external control ──

async def api_scene(request: web.Request) -> web.Response:
    data = await request.json()
    scene.image = data.get("image", scene.image)
    scene.location = data.get("location", scene.location)
    scene.mood = data.get("mood", scene.mood)
    await broadcast({"type": "scene", "scene": asdict(scene)})
    return web.json_response({"ok": True})


async def api_narrative(request: web.Request) -> web.Response:
    data = await request.json()
    if "lines" in data:
        narrative.lines = data["lines"]
    if "current_index" in data:
        narrative.current_index = data["current_index"]
    await broadcast({"type": "narrative", "narrative": asdict(narrative)})
    return web.json_response({"ok": True})


async def api_dice(request: web.Request) -> web.Response:
    data = await request.json()
    dice.visible = data.get("visible", dice.visible)
    dice.value = data.get("value", dice.value)
    dice.skill = data.get("skill", dice.skill)
    dice.target = data.get("target", dice.target)
    dice.character = data.get("character", dice.character)
    dice.success = data.get("success", dice.success)
    await broadcast({"type": "dice", "dice": asdict(dice)})
    return web.json_response({"ok": True})


async def api_characters(request: web.Request) -> web.Response:
    global characters
    data = await request.json()
    if isinstance(data, list):
        characters = [CharacterState(**c) for c in data]
    await broadcast({"type": "characters", "characters": [asdict(c) for c in characters]})
    return web.json_response({"ok": True})


async def api_danmaku(request: web.Request) -> web.Response:
    data = await request.json()
    if "messages" in data:
        danmaku.messages = data["messages"]
    await broadcast({"type": "danmaku", "danmaku": asdict(danmaku)})
    return web.json_response({"ok": True})


async def api_vote(request: web.Request) -> web.Response:
    data = await request.json()
    vote.visible = data.get("visible", vote.visible)
    vote.prompt = data.get("prompt", vote.prompt)
    if "options" in data:
        vote.options = data["options"]
    await broadcast({"type": "vote", "vote": asdict(vote)})
    return web.json_response({"ok": True})


async def api_push_line(request: web.Request) -> web.Response:
    """推送一条旁白文本，自动追加并滚动到最新."""
    data = await request.json()
    text = data.get("text", "")
    narrative.lines.append(text)
    narrative.current_index = len(narrative.lines) - 1
    await broadcast({"type": "narrative", "narrative": asdict(narrative)})
    return web.json_response({"ok": True})


async def api_roll_dice(request: web.Request) -> web.Response:
    """快捷掷骰：发送 value/target/skill/character，自动显示骰子动画."""
    data = await request.json()
    dice.visible = True
    dice.value = data.get("value", 0)
    dice.target = data.get("target", 0)
    dice.skill = data.get("skill", "检定")
    dice.character = data.get("character", "")
    dice.success = dice.value <= dice.target if dice.target > 0 else None
    await broadcast({"type": "dice", "dice": asdict(dice)})
    return web.json_response({"ok": True})


async def api_reset(request: web.Request) -> web.Response:
    """重置所有状态."""
    global scene, narrative, dice, characters, danmaku, vote
    scene = SceneState()
    narrative = NarrativeState()
    dice = DiceState()
    characters = []
    danmaku = DanmakuState()
    vote = VoteState()
    await broadcast(build_state_message())
    return web.json_response({"ok": True})


# ── App factory ──

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)

    # REST API
    app.router.add_post("/api/scene", api_scene)
    app.router.add_post("/api/narrative", api_narrative)
    app.router.add_post("/api/push_line", api_push_line)
    app.router.add_post("/api/dice", api_dice)
    app.router.add_post("/api/roll", api_roll_dice)
    app.router.add_post("/api/characters", api_characters)
    app.router.add_post("/api/danmaku", api_danmaku)
    app.router.add_post("/api/vote", api_vote)
    app.router.add_post("/api/reset", api_reset)

    # Static files
    app.router.add_static("/images", pathlib.Path("/tmp"))

    return app


def main():
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8766)


if __name__ == "__main__":
    main()
