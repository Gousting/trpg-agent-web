"""OBS overlay server — WebSocket 推送 COC 直播数据到浏览器覆盖层."""

from __future__ import annotations

import asyncio
import json
import pathlib
from dataclasses import dataclass, field, asdict
from typing import Optional

import aiohttp
from aiohttp import web

from .scene_matcher import SceneMatcher

OVERLAY_DIR = pathlib.Path(__file__).parent.parent / "docs"
SCENES_DIR = pathlib.Path(__file__).parent.parent / "data" / "scenes" / "Sceneimage"
ITEMS_DIR = pathlib.Path(__file__).parent.parent / "data" / "items" / "Itemimage"
CHARS_DIR = pathlib.Path(__file__).parent.parent / "data" / "characters" / "Userimage"
MODULE_SCENES_DIR = pathlib.Path(__file__).parent.parent / "data" / "scenes" / "modules"
BGM_DIR = pathlib.Path(__file__).parent.parent / "data" / "bgm"
TTS_CACHE = pathlib.Path(__file__).parent.parent / "data" / "tts_cache"

import hashlib
import edge_tts

# ── Scene matcher ──
scene_matcher = SceneMatcher()

# ── Data models ──

@dataclass
class SceneState:
    image: str = ""
    location: str = ""
    mood: str = ""

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
    portrait: str = ""         # 角色头像文件名
    hp: int = 0
    hp_max: int = 0
    san: int = 0
    san_max: int = 0
    luck: int = 0
    luck_max: int = 0
    status: str = ""
    active: bool = False

@dataclass
class ItemState:
    image: str = ""            # 物品图片文件名
    name: str = ""
    item_type: str = ""
    narrative_hook: str = ""   # 叙事钩子
    visible: bool = False      # 控制弹出/隐藏

@dataclass
class DanmakuState:
    messages: list[dict] = field(default_factory=list)

@dataclass
class VoteState:
    visible: bool = False
    prompt: str = ""
    options: list[dict] = field(default_factory=list)

@dataclass
class BgmState:
    track: str = ""            # 当前播放的 BGM 文件名
    volume: float = 0.3        # 音量 0.0-1.0
    playing: bool = True

# ── Global state ──

scene = SceneState()
narrative = NarrativeState()
dice = DiceState()
characters: list[CharacterState] = []
item = ItemState()
danmaku = DanmakuState()
vote = VoteState()
bgm = BgmState()

connected_clients: set[web.WebSocketResponse] = set()

# ── BGM mood mapping ──
_bgm_mappings: dict[str, str] = {}
_bgm_default = "exploration"

# ── TTS ──
TTS_VOICE = "zh-CN-YunyangNeural"  # KP 旁白沉稳男声
TTS_CACHE.mkdir(exist_ok=True)


async def _speak(text: str) -> str | None:
    """生成 TTS 音频，返回可访问的 URL 路径，失败返回 None。"""
    h = hashlib.md5(text.encode()).hexdigest()[:12]
    out = TTS_CACHE / f"{h}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return f"/audio/tts/{h}.mp3"
    try:
        comm = edge_tts.Communicate(text, TTS_VOICE)
        await comm.save(str(out))
        return f"/audio/tts/{h}.mp3" if out.exists() else None
    except Exception:
        return None


def build_state_message() -> dict:
    return {
        "type": "full_sync",
        "scene": asdict(scene),
        "narrative": asdict(narrative),
        "dice": asdict(dice),
        "characters": [asdict(c) for c in characters],
        "item": asdict(item),
        "danmaku": asdict(danmaku),
        "vote": asdict(vote),
        "bgm": asdict(bgm),
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
    await ws.send_str(json.dumps(build_state_message()))
    try:
        async for _ in ws:
            pass
    finally:
        connected_clients.discard(ws)
    return ws


# ── REST API ──

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


async def api_item(request: web.Request) -> web.Response:
    """弹出/隐藏物品卡。visible=true 弹出，visible=false（或缺省）隐藏。

    POST /api/item
    {"image":"xxx.png", "name":"旧日支配者之书", "item_type":"古籍",
     "narrative_hook":"书页间夹着一片干枯的鳞片", "visible":true}
    """
    data = await request.json()
    item.image = data.get("image", item.image)
    item.name = data.get("name", item.name)
    item.item_type = data.get("item_type", item.item_type)
    item.narrative_hook = data.get("narrative_hook", item.narrative_hook)
    item.visible = data.get("visible", False)
    await broadcast({"type": "item", "item": asdict(item)})
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
    data = await request.json()
    text = data.get("text", "")
    narrative.lines.append(text)
    narrative.current_index = len(narrative.lines) - 1

    # Auto-generate TTS
    audio_url = await _speak(text)

    await broadcast({
        "type": "narrative",
        "narrative": asdict(narrative),
        **({"audio_url": audio_url} if audio_url else {}),
    })
    return web.json_response({"ok": True, "audio_url": audio_url})


async def api_roll_dice(request: web.Request) -> web.Response:
    data = await request.json()
    dice.visible = True
    dice.value = data.get("value", 0)
    dice.target = data.get("target", 0)
    dice.skill = data.get("skill", "检定")
    dice.character = data.get("character", "")
    dice.success = dice.value <= dice.target if dice.target > 0 else None
    await broadcast({"type": "dice", "dice": asdict(dice)})
    return web.json_response({"ok": True})


async def api_bgm(request: web.Request) -> web.Response:
    """设置/切换 BGM 音轨。
    POST /api/bgm  {"track": "horror", "volume": 0.3, "playing": true}
    可选 mood 字段自动匹配音轨：{"mood": "恐怖"}
    """
    data = await request.json()
    
    # mood → track 自动匹配
    if "mood" in data and not data.get("track"):
        mood = data["mood"]
        track = _bgm_mappings.get(mood, _bgm_default)
        data["track"] = track
    
    bgm.track = data.get("track", bgm.track)
    bgm.volume = data.get("volume", bgm.volume)
    bgm.playing = data.get("playing", bgm.playing)
    await broadcast({"type": "bgm", "bgm": asdict(bgm)})
    return web.json_response({"ok": True})


async def api_reset(request: web.Request) -> web.Response:
    global scene, narrative, dice, characters, item, danmaku, vote, bgm
    scene = SceneState()
    narrative = NarrativeState()
    dice = DiceState()
    characters = []
    item = ItemState()
    danmaku = DanmakuState()
    vote = VoteState()
    bgm = BgmState()
    await broadcast(build_state_message())
    return web.json_response({"ok": True})


async def api_scene_match(request: web.Request) -> web.Response:
    if not scene_matcher._loaded:
        scene_matcher.load()

    data = await request.json()
    text = data.get("text", "")

    if not text:
        return web.json_response({"ok": False, "error": "missing text"}, status=400)

    matches = scene_matcher.match(text, top_k=3)
    if not matches:
        return web.json_response({"ok": False, "error": "no match", "matches": []})

    best = matches[0]
    scene.image = best.filename
    scene.location = best.location
    scene.mood = best.mood

    # Auto-match BGM from scene mood
    if best.mood:
        b_track = _bgm_mappings.get(best.mood, _bgm_default)
        if b_track != bgm.track:
            bgm.track = b_track
            bgm.playing = True
            await broadcast({"type": "bgm", "bgm": asdict(bgm)})

    await broadcast({"type": "scene", "scene": asdict(scene)})

    return web.json_response({
        "ok": True,
        "matched": {
            "image": best.filename,
            "location": best.location,
            "mood": best.mood,
            "score": best.score,
        },
        "alternatives": [
            {"image": m.filename, "location": m.location, "score": m.score}
            for m in matches[1:]
        ],
    })


def _load_bgm_mappings():
    """加载 BGM mood→track 映射表。"""
    global _bgm_mappings, _bgm_default
    import json as _json
    mappings_file = pathlib.Path(__file__).parent.parent / "data" / "bgm_mappings.json"
    try:
        with open(mappings_file) as f:
            data = _json.load(f)
        _bgm_mappings = data.get("mappings", {})
        _bgm_default = data.get("default", "exploration")
    except Exception:
        _bgm_mappings = {}
        _bgm_default = "exploration"


# ── App factory ──

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)

    app.router.add_post("/api/scene", api_scene)
    app.router.add_post("/api/narrative", api_narrative)
    app.router.add_post("/api/push_line", api_push_line)
    app.router.add_post("/api/dice", api_dice)
    app.router.add_post("/api/roll", api_roll_dice)
    app.router.add_post("/api/characters", api_characters)
    app.router.add_post("/api/item", api_item)
    app.router.add_post("/api/danmaku", api_danmaku)
    app.router.add_post("/api/vote", api_vote)
    app.router.add_post("/api/reset", api_reset)
    app.router.add_post("/api/scene/match", api_scene_match)
    app.router.add_post("/api/bgm", api_bgm)

    app.router.add_static("/images/scenes", SCENES_DIR)
    app.router.add_static("/images/scenes/modules", MODULE_SCENES_DIR)
    app.router.add_static("/images/items", ITEMS_DIR)
    app.router.add_static("/images/characters", CHARS_DIR)
    app.router.add_static("/audio/bgm", BGM_DIR)
    app.router.add_static("/audio/tts", TTS_CACHE)

    n = scene_matcher.load()
    if n > 0:
        import logging
        logger = logging.getLogger(__name__)
        logger.info("SceneMatcher 已加载 %d 个场景（%d 种类型）",
                     n, len(scene_matcher.list_scene_types()))

    # Load BGM mood mappings
    _load_bgm_mappings()

    return app


def main():
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8766)


if __name__ == "__main__":
    main()
