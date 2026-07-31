"""TRPG Agent Web — FastAPI + SSE 流式 COC 跑团。

启动:
    uv run python -m trpg_agent_web.web_server
    # 浏览器打开 http://localhost:8766
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import edge_tts
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.llm.remote_client import RemoteClient
from trpg_agent.memory.game_state import Investigator
from trpg_agent.mapgen import DungeonMap
from trpg_agent.scene_matcher import SceneMatcher
from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.adventure import Adventure
from trpg_agent.combat import CombatLoop

log = logging.getLogger(__name__)

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
MODULES_DIR = DATA_DIR / "modules"
BGM_DIR = DATA_DIR / "bgm"
CHARS_DIR = DATA_DIR / "characters" / "Userimage"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/images/scenes/modules", StaticFiles(directory=str(MODULES_DIR)), name="module_scenes")
app.mount("/images/scenes", StaticFiles(directory=str(SCENES_DIR)), name="scenes")
app.mount("/images/characters", StaticFiles(directory=str(CHARS_DIR)), name="characters")
app.mount("/audio/bgm", StaticFiles(directory=str(BGM_DIR)), name="bgm")

# ── TTS ───────────────────────────────────────
TTS_DIR = DATA_DIR / "tts_cache"
TTS_DIR.mkdir(exist_ok=True)
TTS_VOICE = "zh-CN-YunyangNeural"  # KP 旁白沉稳男声

# ── BGM mood 映射 ──────────────────────────────
_bgm_mappings: dict[str, str] = {}
_bgm_default = "exploration"
try:
    _bgm_data = json.loads((DATA_DIR / "bgm_mappings.json").read_text(encoding="utf-8"))
    _bgm_mappings = _bgm_data.get("mappings", {})
    _bgm_default = _bgm_data.get("default", "exploration")
except Exception:
    pass

app.mount("/audio/tts", StaticFiles(directory=str(TTS_DIR)), name="tts")

# ── 投票同步 ───────────────────────────────
# 投票窗口内累加计数，每次投票通过 Queue 推送 tally 到 SSE 流，
# 前端实时更新票数分布。窗口结束后取多数票。
_vote_tallies: dict[str, dict[str, int]] = {}
_vote_queues: dict[str, asyncio.Queue] = {}
VOTE_WINDOW_SECONDS = 32  # 略长于前端 30s 倒计时，确保超时自动投的那一票也能被计入

# CombatLoop 不解析 LLM 叙事文本判定伤害/撤退——纯叙事无法自然触发结局，
# 回合数超过此上限后强制收尾，避免战斗无限进行。
COMBAT_MAX_ROUNDS = 6

# EXIT 标记：AI 玩家模式下，KP 叙述末尾可附加 <<EXIT n>> 请求移动到第 n 个场景出口
_EXIT_MARKER_RE = re.compile(r"<<\s*EXIT[\s:]*(\d+)\s*>>", re.IGNORECASE)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _is_remote_host(host: str) -> bool:
    """判断是否为远程 API（非 Ollama）。"""
    return "/v1" in host or host.startswith("https://")


def _make_client(host: str, model: str, api_key: str = "", *,
                 num_ctx: int = 8192, timeout: float = 300):
    """根据 host 创建 OllamaClient 或 RemoteClient。"""
    if _is_remote_host(host):
        return RemoteClient(host, model, api_key, timeout=timeout)
    return OllamaClient(host, model, num_ctx=num_ctx, timeout=timeout)


def _scene_matcher() -> SceneMatcher:
    """惰性场景匹配器。"""
    sm = SceneMatcher()
    sm.load()
    return sm


def _state_snapshot(session: Session) -> dict:
    """调查员状态快照。"""
    import sys
    snap = {
        inv.name: {
            "hp": inv.hp, "max_hp": inv.max_hp,
            "san": inv.san, "max_san": inv.max_san,
            "luck": inv.luck,
            "conditions": list(inv.conditions),
            "inventory": list(inv.inventory),
        }
        for inv in session.state.investigators
    }
    print(f"SNAPSHOT: { {n: s['san'] for n,s in snap.items()} }", file=sys.stderr, flush=True)
    return snap


async def _chat_stream(client, system: str, user_msg: str,
                       temperature: float = 0.8, max_tokens: int = 2000):
    """流式聊天，yield token（状态机过滤 GS 标记，最小缓冲）。兼容 OllamaClient 和 RemoteClient。"""
    buf = ""
    in_marker = False
    _GS_START = "<!--GS"
    try:
        async for token in client.chat_stream(
            system, [{"role": "user", "content": user_msg}],
            options={"temperature": temperature, "num_predict": max_tokens},
        ):
            for ch in token:
                buf += ch
                if in_marker:
                    if buf.endswith("-->"):
                        buf = ""
                        in_marker = False
                else:
                    if buf.endswith(_GS_START):
                        safe = buf[:-len(_GS_START)]
                        buf = ""
                        in_marker = True
                        if safe:
                            yield safe
                    elif not _could_be_gs_prefix(buf):
                        yield buf
                        buf = ""
    except Exception as e:
        yield f"\n[错误] {e}"
    if buf and not in_marker:
        yield buf


def _could_be_gs_prefix(s: str) -> bool:
    """检查字符串末尾是否可能是 <!--GS 的前缀。"""
    gs = "<!--GS"
    for i in range(1, len(gs)):
        if s.endswith(gs[:i]):
            return True
    return False


def _match_scene(text: str, *, min_score: float = 1.5) -> dict | None:
    """从叙述文本匹配场景图，低分不返回。"""
    try:
        sm = _scene_matcher()
        matches = sm.match(text, top_k=1, min_score=min_score)
        if matches:
            m = matches[0]
            return {"image": m.filename, "location": m.location, "mood": m.mood, "score": m.score}
    except Exception:
        pass
    return None


async def _speak(text: str) -> str | None:
    """生成 TTS 音频，返回相对 URL 路径。"""
    h = hashlib.md5(text.encode()).hexdigest()[:12]
    out = TTS_DIR / f"{h}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return f"/audio/tts/{h}.mp3"
    try:
        comm = edge_tts.Communicate(text, TTS_VOICE)
        await comm.save(str(out))
        return f"/audio/tts/{h}.mp3" if out.exists() else None
    except Exception:
        return None


def _bgm_for_mood(mood: str) -> str:
    """根据场景氛围匹配 BGM 音轨名。"""
    return _bgm_mappings.get(mood, _bgm_default)


# ── 房间类型 → 场景类型映射 ────────────────────
_ROOM_SCENE_MAP: dict[str, str] = {
    "entrance": "室内场景 - 废弃建筑入口",
    "corridor": "室内走廊",
    "ward": "医院病房",
    "lab": "实验室",
    "office": "书房",
    "storage": "储藏室",
    "basement": "地下室",
    "morgue": "停尸房",
    "ritual": "废弃教堂",
    "library": "书房",
}


def _scene_for_room(room_type: str) -> dict | None:
    """根据房间类型匹配场景图。"""
    try:
        sm = _scene_matcher()
        scene_type = _ROOM_SCENE_MAP.get(room_type, "")
        if scene_type:
            matches = sm.match_exact_scene_type(scene_type)
            if matches:
                m = matches[0]
                return {"image": m.filename, "location": m.location, "mood": m.mood, "score": 1.0}
        # 回退：随机场景
        if sm._images:
            import random
            fname = random.choice(list(sm._images.keys()))
            tags = sm._images[fname]
            return {"image": fname, "location": tags.get("location", ""),
                    "mood": (tags.get("mood", [""]) or [""])[0], "score": 0}
    except Exception:
        pass
    return None


def _room_threat_events(rc: dict, inv_state, speaker: str) -> list[dict]:
    """房间威胁事件：返回 [(type, text), ...]。"""
    events: list[dict] = []
    threats_text = rc.get("threats", "")
    if not threats_text or threats_text == "无":
        return events

    import random
    # 30% 概率触发 SAN 损失
    if random.random() < 0.30:
        loss = random.randint(1, 3)
        inv_state.san = max(0, inv_state.san - loss)
        events.append({
            "type": "san_loss",
            "speaker": speaker,
            "text": f"{speaker} 感受到不可名状的恐怖，SAN -{loss}",
            "amount": loss,
        })
    # 10% 概率触发直接伤害
    if random.random() < 0.10:
        dmg = random.randint(1, 2)
        inv_state.take_damage(dmg)
        events.append({
            "type": "damage",
            "speaker": speaker,
            "text": f"{speaker} 受到了 {dmg} 点伤害",
            "amount": dmg,
        })
    return events


def _dice_consequence(dice_context: str, inv_state) -> dict | None:
    """骰子失败后果。"""
    if "失败" not in dice_context:
        return None
    import random
    # 50% SAN损失，30% HP损失，20% 无损失
    r = random.random()
    if r < 0.5:
        loss = random.randint(1, 2)
        old = inv_state.san
        inv_state.san = max(0, inv_state.san - loss)
        import sys
        print(f"DICE_CONS: {inv_state.name} SAN {old}→{inv_state.san} (r={r:.3f} loss={loss})", file=sys.stderr, flush=True)
        return {"type": "san_loss", "amount": loss, "text": f"SAN -{loss}"}
    elif r < 0.8:
        dmg = 1
        inv_state.take_damage(dmg)
        log.info("DICE_CONS: %s HP -%d", inv_state.name, dmg)
        return {"type": "damage", "amount": dmg, "text": f"HP -{dmg}"}
    return None


def _investigators_state_text(session: Session) -> str:
    """生成调查员状态摘要文本，供 CombatLoop 的战斗 prompt 使用。"""
    parts = []
    for inv in session.state.investigators:
        cond = f"（{', '.join(inv.conditions)}）" if inv.conditions else ""
        parts.append(f"{inv.name} HP {inv.hp}/{inv.max_hp} SAN {inv.san}/{inv.max_san}{cond}")
    return "；".join(parts)


async def _vote_window(sid: str):
    """投票窗口——推送实时票数变化，最后一次 yield 是 ('__result__', tally)。

    共享给常规投票和战斗投票两种场景使用。
    """
    _vote_tallies[sid] = {}
    vote_queue: asyncio.Queue = asyncio.Queue()
    _vote_queues[sid] = vote_queue
    deadline = asyncio.get_event_loop().time() + VOTE_WINDOW_SECONDS
    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(vote_queue.get(), timeout=remaining)
                current_tally = _vote_tallies.get(sid, {})
                yield ("vote_tally", {"tally": dict(current_tally)})
            except asyncio.TimeoutError:
                break
    finally:
        _vote_queues.pop(sid, None)
    tally = _vote_tallies.pop(sid, {})
    yield ("__result__", tally)


async def event_stream(host: str, kp_model: str, player_model: str,
                       turns: int, seed: int | None, mode: str,
                       compose_modules: bool = False,
                       kp_api_key: str = "",
                       force_pickup: bool = False):
    """SSE 事件流 — 完整的游戏循环。"""
    
    # ── 模块组合模式 ──────────────────────────
    adventure: Adventure | None = None
    if compose_modules:
        yield _sse("status", {"text": "组合模块剧情..."})
        composer = ModuleComposer(Path(__file__).resolve().parent.parent / "data" / "modules")
        composer.load_all()
        bundle = composer.compile(seed=seed, max_depth=5)
        adventure = bundle.adventure
        yield _sse("status", {"text": f"已组合 {len(bundle.module_ids)} 个模块, {len(adventure._scenes)} 个场景"})
    
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

    # ── 模型检查（仅 Ollama）──────────────────
    use_remote = _is_remote_host(host)
    if not use_remote:
        try:
            async with httpx.AsyncClient(timeout=5) as cl:
                resp = await cl.get(f"{host}/api/tags")
                available = [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            available = []
        if available and kp_model not in available:
            yield _sse("error", {"text": f"KP模型 {kp_model} 不可用"})
            return

    kp_client = _make_client(host, kp_model, kp_api_key, timeout=300)

    # ── Session ───────────────────────────────
    sid = f"web_{datetime.now().strftime('%m%d_%H%M%S')}"
    old_dir = Path("data/sessions") / sid
    if old_dir.exists():
        shutil.rmtree(old_dir)
    session = Session(sid, auto_save_interval=0, max_context=8192)

    for inv_data in INVESTIGATORS:
        # 避免重复：characters.json 可能已经加载过
        existing = session.state.find_investigator(inv_data["name"])
        if existing:
            existing.hp = inv_data.get("hp", existing.hp)
            existing.san = inv_data.get("san", existing.san)
            existing.max_hp = inv_data.get("max_hp", existing.max_hp)
            existing.max_san = inv_data.get("max_san", existing.max_san)
            existing.luck = inv_data.get("luck", existing.luck)
        else:
            inv = Investigator(
                name=inv_data["name"], hp=inv_data["hp"], max_hp=inv_data["max_hp"],
                san=inv_data["san"], max_san=inv_data["max_san"], luck=inv_data["luck"],
                skills=inv_data["skills"], inventory=list(inv_data.get("inventory", [])),
            )
            session.state.investigators.append(inv)
    session.state.location = current_room.name if current_room else ""

    # ── 模块模式：注入冒险上下文 ──────────────
    opening_text = OPENING
    if adventure is not None:
        start_scene = adventure.get_scene(adventure.start_scene)
        if start_scene is not None:
            opening_text = start_scene.description
            session.state.scene_id = adventure.start_scene
            session.state.location = start_scene.title
            session.state.adventure_id = adventure.id
            # 注入模块 NPC
            from trpg_agent.memory.game_state import Npc
            for npc_name in adventure.npc_names():
                if not session.state.find_npc(npc_name):
                    nd = adventure.get_npc(npc_name)
                    if nd:
                        session.state.npcs.append(Npc(
                            name=nd.name, attitude=nd.attitude,
                            description=nd.description,
                            location=adventure.start_scene,
                        ))

    yield _sse("init", {
        "investigators": INVESTIGATORS,
        "kp_model": kp_model, "player_model": player_model,
        "opening": opening_text, "room": rc, "mode": mode,
        "compose_modules": compose_modules,
    })

    # ── KP 开场（流式）────────────────────────
    # 根据初始房间类型选场景图
    initial_room_type = current_room.room_type if current_room else "entrance"
    scene_info = _scene_for_room(initial_room_type)
    # 模块模式：用模块场景图覆盖
    if adventure is not None:
        start_scene2 = adventure.get_scene(adventure.start_scene)
        if start_scene2 and start_scene2.image:
            scene_info = {**scene_info, "image": start_scene2.image} if scene_info else {"image": start_scene2.image, "mood": "dread"}
    bgm_track = _bgm_for_mood(scene_info["mood"]) if scene_info else _bgm_default
    
    system_prompt = session.build_system_prompt(adventure=adventure)
    # 注入 COC 恐怖氛围指令
    scene_context = start_scene.description if (adventure and (start_scene := adventure.get_scene(adventure.start_scene))) else OPENING
    coc_directive = (
        f"当前场景：{scene_context}\n"
        f"位置：{rc['name']} — {rc['desc']}\n"
        f"调查员：{', '.join(i['name'] for i in INVESTIGATORS)}\n"
        f"线索：{rc.get('clues', '暂无')}\n"
        f"⚠ 威胁：{rc.get('threats', '无')}\n\n"
        "【重要指令】\n"
        "1. 你是克苏鲁的呼唤守秘人，营造宇宙恐怖氛围——人类渺小、真相可怖、理智侵蚀。\n"
        "2. 描述中必须包含感官细节：声音、气味、触感、光线扭曲。\n"
        "3. 如果房间有威胁，必须在叙述中暗示它——让玩家感到不安。\n"
        "4. 如果提到线索，让它显得诡异而非寻常。\n"
        "5. 叙述控制在3-5句，营造紧张氛围后把选择交还玩家。"
    )
    yield _sse("kp_stream_start", {})
    opening_text = ""
    async for token in _chat_stream(kp_client, system_prompt, coc_directive,
                                    temperature=0.8, max_tokens=4000):
        opening_text += token
        yield _sse("kp_token", {"text": token})
    if not opening_text:
        opening_text = f"你们站在{current_room.name}中。{current_room.description}"
    session.record_turn("(游戏开始)", opening_text)

    # TTS + BGM
    audio_url = await _speak(opening_text[:500])
    yield _sse("kp_stream_end", {
        "state": _state_snapshot(session),
        "scene": scene_info,
        "audio_url": audio_url,
        "bgm_track": bgm_track,
    })
    await asyncio.sleep(0.5)

    # ── 游戏循环 ──────────────────────────────
    last_narration = opening_text
    player_order = [inv["name"] for inv in INVESTIGATORS]
    current_bgm = bgm_track
    current_scene = scene_info
    combat_loop: CombatLoop | None = None  # 非空时表示当前正处于战斗遭遇场景

    for turn in range(turns):
        speaker = player_order[turn % len(player_order)]
        inv_data = next(inv for inv in INVESTIGATORS if inv["name"] == speaker)
        inv_state = session.state.find_investigator(speaker)
        rc = dmap.room_context()

        # ── 战斗场景检测：命中 combat.enabled 的场景时，整回合交给 CombatLoop 驱动 ──
        current_combat_scene = None
        if adventure is not None:
            _scene_here = adventure.get_scene(session.state.scene_id)
            if _scene_here is not None and _scene_here.combat and _scene_here.combat.get("enabled"):
                current_combat_scene = _scene_here

        if current_combat_scene is not None:
            if combat_loop is None:
                encounter = current_combat_scene.combat["encounter"]
                combat_loop = CombatLoop(encounter, investigators_state=_investigators_state_text(session))
                yield _sse("status", {"text": f"⚔️ 战斗开始：{encounter.title}"})

            # ── 进场/回合叙事 + 三选项（LLM 生成）──
            enter_sys = combat_loop.build_enter_prompt()
            enter_usr = combat_loop.build_enter_user_prompt()
            yield _sse("kp_stream_start", {})
            enter_output = ""
            async for token in _chat_stream(kp_client, enter_sys, enter_usr,
                                            temperature=0.85, max_tokens=1200):
                enter_output += token
                yield _sse("kp_token", {"text": token})
            if not enter_output:
                enter_output = (
                    "（战斗陷入僵局……）\n---\n**观望局势**\n静观其变，等待时机。"
                    "\n---\n**主动出击**\n冒险突进，正面对抗。\n---\n**且战且退**\n边打边撤，寻找生路。"
                )
            round_state = combat_loop.start_round(enter_output)
            audio_url = await _speak(round_state.opening_narration[:500])
            yield _sse("kp_stream_end", {
                "state": _state_snapshot(session),
                "scene": None,
                "audio_url": audio_url,
                "bgm_track": None,
            })

            # ── 展示投票：三个赌注式选项 ──
            vote_options = {opt.option_key.lower(): opt.label for opt in round_state.options}
            if not vote_options:
                vote_options = {"a": "观望局势", "b": "主动出击", "c": "且战且退"}
            yield _sse("vote", {
                "options": vote_options,
                "session_id": sid,
                "speaker": speaker,
            })

            tally: dict[str, int] = {}
            async for evt_name, evt_data in _vote_window(sid):
                if evt_name == "__result__":
                    tally = evt_data
                else:
                    yield _sse(evt_name, evt_data)
            if tally:
                choice = max(vote_options.keys(), key=lambda k: tally.get(k, 0))
            else:
                choice = next(iter(vote_options.keys()), "a")

            chosen_opt = combat_loop.submit_vote(choice.upper())
            action = f"（全员选择了「{chosen_opt.label if chosen_opt else vote_options.get(choice, '')}」）"
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            yield _sse("player_token", {"text": action, "speaker": speaker})
            yield _sse("player_stream_end", {"speaker": speaker})
            await asyncio.sleep(0.2)

            # ── 机制层结算：代码掷骰、扣血、判定结局 ──
            mech_result = combat_loop.run_mechanics()

            # ── 叙事层：LLM 根据机制结果润色叙述 ──
            res_sys = combat_loop.build_resolve_prompt()
            res_usr = combat_loop.build_resolve_user_prompt(mech_result=mech_result)
            yield _sse("kp_stream_start", {})
            resolution_output = ""
            async for token in _chat_stream(kp_client, res_sys, res_usr,
                                            temperature=0.8, max_tokens=1500):
                resolution_output += token
                yield _sse("kp_token", {"text": token})
            if not resolution_output:
                resolution_output = f"（{mech_result.summary}）"
            outcome = combat_loop.resolve(resolution_output, mech_result=mech_result)
            narration = resolution_output

            # 极端兜底：机制层也没触发结局时用 force_end
            if outcome is None and combat_loop.current_round >= COMBAT_MAX_ROUNDS:
                encounter = current_combat_scene.combat["encounter"]
                fallback_id = next(
                    (oid for oid in ("flee", "defeat", "victory") if oid in encounter.outcomes),
                    next(iter(encounter.outcomes), ""),
                )
                if fallback_id:
                    outcome = combat_loop.force_end(fallback_id)
                    narration += "\n\n（战斗持续过久，局势再也无法维持……）"

            if outcome is not None:
                end_text = combat_loop.end_summary()
                yield _sse("kp_token", {"text": "\n\n" + end_text})
                narration += "\n\n" + end_text

            session.record_turn(action, narration, speaker=speaker)
            last_narration = narration
            audio_url = await _speak(narration[:500])

            moved_scene = None
            if outcome is not None:
                # 战斗结束——按结局 ID 跳转到对应的结局过渡场景，走完模块内自动过渡
                module_id = current_combat_scene.id.split("::", 1)[0]
                target_scene_id = f"{module_id}::combat_{outcome.id}"
                moved_scene = session.move_to_scene(target_scene_id, adventure)
                if moved_scene is not None:
                    moved_scene, _trans_meta = _auto_advance_transitions(session, adventure, moved_scene)
                combat_loop = None

            # ── 场景切换（仅战斗结束且成功跳转时）/ 收尾 ──
            scene_changed = False
            bgm_changed = False
            new_bgm = current_bgm
            room_change = None
            if moved_scene is not None:
                session.state.location = moved_scene.title
                current_scene = {"image": moved_scene.image, "location": moved_scene.title, "mood": moved_scene.mood}
                scene_changed = True
                if moved_scene.mood:
                    track = _bgm_for_mood(moved_scene.mood)
                    if track != current_bgm:
                        new_bgm = track
                        current_bgm = track
                        bgm_changed = True
                room_change = {
                    "room_id": None,
                    "room_name": moved_scene.title,
                    "room_desc": moved_scene.description,
                    "items": [],
                    "map": dmap.to_dict(),
                    "grid": dmap.grid,
                    "image": moved_scene.image or dmap.relative_path,
                    "room": rc,
                }
                yield _sse("room_change", room_change)
                await asyncio.sleep(0.5)

            yield _sse("kp_stream_end", {
                "state": _state_snapshot(session),
                "scene": current_scene if scene_changed else None,
                "audio_url": audio_url,
                "bgm_track": new_bgm if bgm_changed else None,
                "room_change": room_change,
                "room": rc,
            })
            await asyncio.sleep(0.3)
            continue  # 战斗回合自成一体，跳过下方常规行动/叙述流程

        # ── 玩家行动 ───────────────────────────
        moved_scene = None  # 投票驱动的模块场景切换结果（仅模块模式下可能非 None）
        trans_meta = None   # 过渡元数据链（供 KP 过渡指令构建）
        if mode == "ai":
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            action = ""
            # force_pickup: 首轮强制拾取房间物品
            if force_pickup and turn == 0:
                room_items = dmap.current_room.items if dmap.current_room else []
                if room_items:
                    action = f"{speaker}捡起{room_items[0]}"
                    yield _sse("player_token", {"text": action, "speaker": speaker})
                else:
                    action = f"（{speaker} 谨慎地观察四周）"
                    yield _sse("player_token", {"text": action, "speaker": speaker})
            else:
                async for token in _ai_player_stream(host, player_model, inv_data, inv_state,
                                                      rc, last_narration, speaker):
                    action += token
                    yield _sse("player_token", {"text": token, "speaker": speaker})
                if not action.strip():
                    action = f"（{speaker} 谨慎地观察四周）"
                    yield _sse("player_token", {"text": action, "speaker": speaker})
            yield _sse("player_stream_end", {"speaker": speaker})
            await asyncio.sleep(0.2)
        else:
            # 人类模式 / 投票模式：展示选项，等待前端投票
            vote_targets: dict[str, str] = {}
            if adventure is not None:
                # 模块模式：投票选项 = 当前场景的真实出口，投票结果将驱动场景切换
                exits = adventure.scene_exits(
                    session.state.scene_id, resolved_ids=session.state.resolved_elements,
                )
                letters = ["a", "b", "c"]
                vote_options = {}
                for letter, exit_view in zip(letters, exits):
                    vote_options[letter] = exit_view.label
                    vote_targets[letter] = exit_view.target_id
                # 出口不足 3 个时，用不跳转场景的自由行动选项补齐按键
                fillers = ["继续深入调查当前场景", "谨慎地观察四周", "与同伴商议下一步行动"]
                for letter, filler in zip(letters[len(vote_options):], fillers):
                    vote_options[letter] = filler
            else:
                vote_options = {
                    "a": "深入调查当前的线索",
                    "b": "与同伴讨论接下来的行动",
                    "c": "谨慎地搜索房间的每个角落",
                }
                # 用当前房间场景定制选项
                room_name = rc.get("name", "") if rc else ""
                if "办公室" in room_name or "书房" in room_name:
                    vote_options = {"a": "翻查桌上的文件和信件", "b": "检查书架后的隐藏空间", "c": "仔细观察墙上的照片和地图"}
                elif "走廊" in room_name:
                    vote_options = {"a": "贴着墙壁缓慢前进", "b": "检查地面的脚印和痕迹", "c": "倾听周围的异常声响"}
                elif "病房" in room_name or "医院" in room_name:
                    vote_options = {"a": "查看病床上的约束带痕迹", "b": "翻阅床头柜的病历记录", "c": "检查窗户是否通向外部"}

            yield _sse("vote", {
                "options": vote_options,
                "session_id": sid,
                "speaker": speaker,
            })

            # 投票窗口：Queue 驱动循环，每次投票推送 tally 给前端，窗口结束后取多数票
            tally: dict[str, int] = {}
            async for evt_name, evt_data in _vote_window(sid):
                if evt_name == "__result__":
                    tally = evt_data
                else:
                    yield _sse(evt_name, evt_data)
            if tally:
                choice = max(vote_options.keys(), key=lambda k: tally.get(k, 0))
            else:
                choice = "a"

            # 投票结果驱动模块场景切换——只有选中真实出口时才会真正移动
            target_scene_id = vote_targets.get(choice)
            if adventure is not None and target_scene_id:
                moved_scene = session.move_to_scene(target_scene_id, adventure)
                if moved_scene is not None:
                    moved_scene, trans_meta = _auto_advance_transitions(session, adventure, moved_scene)

            action = f"（{speaker} 选择了「{vote_options.get(choice, '')}」）"
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            yield _sse("player_token", {"text": action, "speaker": speaker})
            yield _sse("player_stream_end", {"speaker": speaker})
            await asyncio.sleep(0.2)

        # ── 房间物品拾取 ───────────────────────
        items_picked = _handle_pickup(dmap, inv_state, action)
        if items_picked:
            yield _sse("item_pickup", {
                "speaker": speaker, "items": items_picked,
                "inventory": list(inv_state.inventory),
            })

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

        # ── 骰子后果 ───────────────────────────
        consequence = _dice_consequence(dice_context, inv_state) if dice_context else None
        if consequence:
            yield _sse(consequence["type"], {
                "speaker": speaker, "text": consequence.get("text", ""),
                "amount": consequence.get("amount", 0),
            })

        # ── KP 叙述（流式）────────────────────
        system_prompt = session.build_system_prompt(adventure=adventure)
        context_parts = []
        # 房间线索 + 威胁注入
        room_clues = rc.get("clues", "")
        room_threats = rc.get("threats", "")
        if room_clues:
            context_parts.append(f"[房间线索] {room_clues}")
        if room_threats and room_threats != "无":
            context_parts.append(f"[房间威胁] {room_threats}")

        if dice_context:
            is_success = "成功" in dice_context and "失败" not in dice_context
            who = dice_result.get("character", speaker)
            skill = dice_result.get("skill", "行动")
            if is_success:
                directive = (
                    f"[系统检定结果]\n"
                    f"{who} 的「{skill}」检定：成功——{dice_context}\n"
                    f"你必须叙述他/她成功了。描述具体发现/做到/说服了什么。"
                )
            else:
                directive = (
                    f"[系统检定结果]\n"
                    f"{who} 的「{skill}」检定：失败——{dice_context}\n"
                    f"你必须叙述他/她失败了，并描述由此产生的危险后果。"
                )
                if consequence:
                    directive += f"\n此外，他/她还承受了：{consequence.get('text', '')}"
            context_parts.append(directive)
        if items_picked:
            context_parts.append(f"[获得物品] {', '.join(items_picked)}")
        # 模块模式：投票已经把队伍移动到了新场景——告知 KP 描述抵达
        if moved_scene is not None:
            if trans_meta:
                # 有过渡元数据 → 构建结构化 KP 过渡指令
                tm = trans_meta[0]  # 使用第一条过渡元数据（通常只有一条）
                from_type_label = {"combat": "战斗", "exploration": "调查", "story": "剧情"}.get(tm.get("from_type", ""), tm.get("from_type", ""))
                to_type_label = {"combat": "战斗", "exploration": "调查", "story": "剧情"}.get(tm.get("to_type", ""), tm.get("to_type", ""))
                trans_parts = [
                    "[场景过渡]",
                    f"队伍离开了「{tm['from_title']}」（{from_type_label}场景），现在来到了「{tm['to_title']}」（{to_type_label}场景）。",
                ]
                if tm.get("to_desc"):
                    trans_parts.append(f"抵达后的景象：{tm['to_desc']}")
                trans_parts.append(
                    "请用2-3句话叙述从离开到抵达的自然过渡。如果是不同类型场景的切换（如调查→战斗），"
                    "叙述应体现氛围升级；同类型场景切换用简洁的1-2句即可。不要使用模板化句式。"
                )
                context_parts.append("\n".join(trans_parts))
            else:
                context_parts.append(f"[场景切换] 队伍来到了新地点——{moved_scene.title}：{moved_scene.description}")
        # 模块模式 + AI 玩家：告知可用的场景出口，允许 KP 用 <<EXIT n>> 请求移动
        if adventure is not None and mode == "ai":
            current_exits = adventure.scene_exits(
                session.state.scene_id, resolved_ids=session.state.resolved_elements,
            )
            if current_exits:
                exit_lines = "\n".join(f"{i}. {e.label}" for i, e in enumerate(current_exits, 1))
                context_parts.append(
                    "[场景出口]\n" + exit_lines +
                    "\n如果调查员的行动自然会让队伍前往上述某个地点，在叙述最后另起一行附加"
                    " <<EXIT 序号>>（如 <<EXIT 1>>）；如果队伍仍留在当前场景，不要添加这个标记。"
                )
        context_parts.append(f"[{speaker}] {action}")
        kp_user = "\n\n".join(context_parts) + "\n\n请叙述结果："

        yield _sse("kp_stream_start", {})
        narration = ""
        async for token in _chat_stream(kp_client, system_prompt, kp_user,
                                        temperature=0.8, max_tokens=4000):
            narration += token
            yield _sse("kp_token", {"text": token})
        if not narration:
            narration = "（KP 沉思……）"

        # 模块模式 + AI 玩家：解析 KP 叙述里的 <<EXIT n>>，驱动场景切换
        if adventure is not None and mode == "ai":
            m = _EXIT_MARKER_RE.search(narration)
            if m:
                narration = _EXIT_MARKER_RE.sub("", narration).rstrip()
                exit_choice = int(m.group(1))
                current_exits = adventure.scene_exits(
                    session.state.scene_id, resolved_ids=session.state.resolved_elements,
                )
                if 1 <= exit_choice <= len(current_exits):
                    moved_scene = session.move_to_scene(
                        current_exits[exit_choice - 1].target_id, adventure,
                    )
                    if moved_scene is not None:
                        moved_scene, trans_meta = _auto_advance_transitions(session, adventure, moved_scene)

        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration

        # TTS
        audio_url = await _speak(narration[:500])

        # ── 房间移动检测 ───────────────────────
        scene_changed = False
        bgm_changed = False
        new_bgm = current_bgm
        if adventure is not None and moved_scene is not None:
            # 模块模式：场景切换已由投票结果 / <<EXIT n>> 驱动（session.move_to_scene），
            # 这里只需把新场景同步给前端，不再依赖地牢地图的房间名检测
            session.state.location = moved_scene.title
            current_scene = {"image": moved_scene.image, "location": moved_scene.title, "mood": moved_scene.mood}
            scene_changed = True
            # 新场景带 mood → 按氛围切换 BGM
            if moved_scene.mood:
                track = _bgm_for_mood(moved_scene.mood)
                if track != current_bgm:
                    new_bgm = track
                    current_bgm = track
                    bgm_changed = True
            room_change = {
                "room_id": None,
                "room_name": moved_scene.title,
                "room_desc": moved_scene.description,
                "items": [],
                "map": dmap.to_dict(),
                "grid": dmap.grid,
                "image": moved_scene.image or dmap.relative_path,
                "room": rc,
            }
            yield _sse("room_change", room_change)
            await asyncio.sleep(0.5)
        else:
            room_change = _detect_move(dmap, action)
            if room_change:
                session.state.location = room_change.get("room_name", "")
                # 新房间 → 按房间类型选场景图 + BGM
                new_room = dmap.current_room
                if new_room:
                    scene_info = _scene_for_room(new_room.room_type)
                    if scene_info and (current_scene is None or scene_info["image"] != current_scene.get("image")):
                        current_scene = scene_info
                        scene_changed = True
                        # 新场景 → 检查 BGM
                        track = _bgm_for_mood(scene_info.get("mood", ""))
                        if track != current_bgm:
                            new_bgm = track
                            current_bgm = track
                            bgm_changed = True
                # 新房间威胁
                new_rc = dmap.room_context()
                for inv_data_i in INVESTIGATORS:
                    inv_s = session.state.find_investigator(inv_data_i["name"])
                    if inv_s:
                        threat_events = _room_threat_events(new_rc, inv_s, inv_data_i["name"])
                        for evt in threat_events:
                            yield _sse(evt["type"], {
                                "speaker": evt["speaker"],
                                "text": evt["text"],
                                "amount": evt.get("amount", 0),
                            })
                yield _sse("room_change", room_change)
                await asyncio.sleep(0.5)

        # 无场景变化时，不推 scene
        yield _sse("kp_stream_end", {
            "state": _state_snapshot(session),
            "scene": current_scene if scene_changed else None,
            "audio_url": audio_url,
            "bgm_track": new_bgm if bgm_changed else None,
            "room_change": room_change,
            "room": rc,
        })

        await asyncio.sleep(0.3)

    yield _sse("done", {"summary": session.state.scene_summary()})


async def _ai_player_stream(host: str, player_model: str,
                           inv_data: dict, inv_state, rc: dict,
                           last_narration: str, speaker: str):
    """流式生成 AI 玩家行动，逐 token yield。"""
    skills_str = json.dumps(inv_state.skills, ensure_ascii=False)
    items_str = ", ".join(inv_state.inventory) if inv_state.inventory else "无"
    player_system = (
        f"你是 {inv_data['name']}，克苏鲁的呼唤调查员。\n"
        f"HP:{inv_state.hp}/{inv_state.max_hp} SAN:{inv_state.san}/{inv_state.max_san}\n"
        f"技能：{skills_str}  已有物品：{items_str}\n\n"
        f"当前房间：{rc['name']} — {rc['desc']}\n"
        f"出口：{rc['exits']}\n"
        f"⚠ 房间里有这些东西可以拿：{rc['items']}\n"
        f"威胁：{rc['threats']}\n\n"
        "用第一人称描述行动，1-2句话。优先探索房间里的物品（说出物品名），"
        "其次是调查环境或应对威胁。不要替其他调查员说话。"
    )
    player_msg = f"主持人叙述：{last_narration[:600]}\n\n{inv_data['name']}的行动："
    player_client = OllamaClient(host, player_model, num_ctx=4096, timeout=120)
    try:
        async for token in player_client.chat_stream(
            player_system, [{"role": "user", "content": player_msg}],
            options={"temperature": 0.9},
        ):
            yield token
    except Exception:
        pass
    finally:
        await player_client.aclose()


def _handle_pickup(dmap: DungeonMap, inv_state, action: str) -> list[str]:
    """处理房间物品拾取——精确匹配 + 常见简称。"""
    items_picked: list[str] = []
    room = dmap.current_room
    if not room or not room.items:
        return items_picked
    for item in list(room.items):
        # 精确匹配 或 物品名被简称为最后一词（如 "手电筒电池" → "电池"）
        last_word = item  # 中文没有空格分词，取后半段
        if len(item) > 2:
            last_word = item[len(item)//2:]  # 取物品名后半作为简称
        if item in action or last_word in action:
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


def _auto_advance_transitions(session: Session, adventure: Adventure, scene):
    """自动走完模块间的 __trans__ 过渡场景（无需玩家互动），直到落在一个真实场景为止。
    
    Returns:
        (final_scene, transitions_meta) — transitions_meta 是沿途收集的过渡元数据列表，
        供 web_server 构建 KP 过渡指令。
    """
    transitions_meta = []
    guard = 0
    while scene is not None and scene.id.startswith("__trans__") and scene.leads_to and guard < 5:
        if getattr(scene, 'transition', None):
            transitions_meta.append(scene.transition)
        next_scene = session.move_to_scene(scene.leads_to[0], adventure)
        if next_scene is None:
            break
        scene = next_scene
        guard += 1
    return scene, transitions_meta


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
    compose_modules: bool = True,
    kp_api_key: str = "",
    force_pickup: bool = False,
):
    try:
        seed_val = int(seed) if seed else None
    except ValueError:
        seed_val = None
    return StreamingResponse(
        event_stream(host, kp, player, turns, seed_val, mode, compose_modules,
                     kp_api_key=kp_api_key, force_pickup=force_pickup),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 投票端点 ───────────────────────────────

class VoteRequest(BaseModel):
    choice: str  # "a" | "b" | "c"
    counts: dict = {}  # {a: 0, b: 0, c: 0}
    session_id: str = ""


@app.post("/api/vote")
async def handle_vote(req: VoteRequest):
    """接收前端投票请求（键盘 a/b/c 或点击）。
    只累加到该场次的投票计数里；真正的选择由投票窗口结束后的多数票决定
    （见 event_stream 里的 VOTE_WINDOW_SECONDS 等待逻辑），而不是第一票立即生效。
    忽略客户端自报的 counts，避免信任前端本地计数。
    """
    sid = req.session_id
    if not sid:
        # 无 session_id 时使用最近一个仍在进行中的投票
        for _sid in list(_vote_tallies.keys()):
            sid = _sid
            break
    if sid and sid in _vote_tallies:
        tally = _vote_tallies[sid]
        tally[req.choice] = tally.get(req.choice, 0) + 1
        log.info("投票: session=%s choice=%s tally=%s", sid, req.choice, tally)
        # 推送到 SSE 流，实时通知所有客户端
        if sid in _vote_queues:
            try:
                _vote_queues[sid].put_nowait(tally.copy())
            except asyncio.QueueFull:
                pass
        return {"accepted": True, "choice": req.choice, "tally": tally}
    return {"accepted": False, "reason": "no active vote"}


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
