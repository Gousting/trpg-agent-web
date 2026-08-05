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
import random
import re
import shutil
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import edge_tts
import httpx
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.llm.remote_client import RemoteClient
from trpg_agent.llm.sanitize import _sanitize
from trpg_agent.llm import echo_guard, consistency, intro_guard
from trpg_agent.memory.game_state import Investigator
from trpg_agent.mapgen import DungeonMap
from trpg_agent.scene_matcher import SceneMatcher
from trpg_agent.adventure.module_composer import ModuleComposer, _combat_to_scene
from trpg_agent.adventure import Adventure
from trpg_agent.combat.orchestrator import CombatOrchestrator
from trpg_agent.combat.encounter import CombatEncounter

# ── 无限流轮回者存档目录（T6）──────────────────
PROFILE_DIR = Path("data/infinite_flow/profiles")

log = logging.getLogger(__name__)

# KP 叙述质量守卫（原 orchestrator.py 的输出清洗/防复读/一致性检查，接入 Web 流式循环）：
# - _sanitize()：去掉小模型的角色标签泄露/元话语开场白/自纠正框架等（已是中文本地化版本）。
# - echo_guard：检测复读玩家的话 / 复述自己上一轮描述。
# - consistency：检测"死亡或不在场的 NPC 说话"——注意 GameState.Npc 目前不跟踪生死，
#   所以"死亡 NPC"这一半检查恒为假（安全空操作）；"不在场"这一半用 scene.npcs_here 比对有效。
# - intro_guard：仅用于开场白，检测过短/漏掉角色。
# 这几个守卫原本是给"真流式"（逐 token 发送）设计的——违规只能事后记录，无法撤回已经发给
# 玩家的文字。这里改为"整段生成 → 检查 → 必要时重试一次 → 再假打字机效果发送"，
# 换来了违规时真正重新生成一次的能力（详见 _chat_generate/_fake_stream/_check_kp_narration）。
_FAKE_STREAM_CHUNK = 3
_FAKE_STREAM_DELAY = 0.015

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

# 无限流：AI 队友复用调查员数值模板（docs/infinite-flow-v2-design.md §5——队友不参与
# AP/强化，HP/技能简化，战斗沿用现有调查员模板数值）。主控固定为轮回者，因此排除默认
# 主控占位的第一位（陈明），固定用后两位作为无限流场景下的常驻 AI 队友。
INFINITE_FLOW_TEAMMATES: list[dict] = INVESTIGATORS[1:]

# 哈利波特：独立的 1 主控 + 2 NPC 队友小队（原创同人角色，不使用官方正典人物名，
# 避免版权问题）。字段形状与 INVESTIGATORS 一致，供 _roster_for_world() 按 world 选用。
HARRY_POTTER_ROSTER: list[dict] = [
    {"name": "凯尔", "hp": 12, "max_hp": 12, "san": 60, "max_san": 60, "luck": 50,
     "skills": {"黑魔法防御": 55, "胆识": 60, "缴械咒": 50, "飞行": 45, "魔咒学": 40},
     "inventory": ["魔杖", "分院徽章", "隐形斗篷残片"],
     "portrait": "", "color": "#7f0909"},
    {"name": "艾米", "hp": 10, "max_hp": 10, "san": 70, "max_san": 70, "luck": 45,
     "skills": {"魔法史": 60, "草药学": 55, "预言占卜": 30, "魔咒学": 50},
     "inventory": ["羽毛笔", "古老魔法书", "护身符"],
     "portrait": "", "color": "#0e6ba8"},
    {"name": "托马斯", "hp": 15, "max_hp": 15, "san": 40, "max_san": 40, "luck": 55,
     "skills": {"魔药学": 50, "保护咒": 45, "体能": 55, "忠诚感知": 40},
     "inventory": ["魔药瓶", "护身符项链", "干粮"],
     "portrait": "", "color": "#f0c75e"},
]

# 世界观 → 角色名单映射；未登记的 world 一律回退 COC 默认名单。
WORLD_ROSTERS: dict[str, list[dict]] = {
    "harry_potter": HARRY_POTTER_ROSTER,
}


def _roster_for_world(world: str) -> list[dict]:
    """按世界观返回「1 主控 + 2 NPC 队友」角色名单，未匹配的世界观回退 COC 默认名单。"""
    return WORLD_ROSTERS.get((world or "").strip().lower(), INVESTIGATORS)


OPENING = "1928年深秋，你们收到匿名信，来到阿卡姆郊外的废弃疗养院。推开吱呀作响的大门，你们踏入了这座被诅咒的建筑。"

# ═══════════════════════════════════════════════════════
# Web 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(title="COC TRPG 跑团")

# ── 限流 ───────────────────────────────
# 极简单的内存滑动窗口限流，只作用于 /api/ 前缀接口，防止单 IP 高频刷投票/重复建流。
# 注意：进程内存状态，多进程/多实例部署时需换成 Redis 等共享存储的方案。
_RATE_LIMIT_WINDOW_SECONDS = 10.0
_RATE_LIMIT_MAX_REQUESTS = 20  # 每 IP 每窗口对 /api/* 的最大请求数
_rate_limit_buckets: dict[str, deque] = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            client_ip = request.client.host if request.client else "unknown"
            now = time.monotonic()
            bucket = _rate_limit_buckets.setdefault(client_ip, deque())
            while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
                return JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)
            bucket.append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware)

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
    log.warning("加载 bgm_mappings.json 失败，BGM 将全部回退到默认曲目", exc_info=True)

app.mount("/audio/tts", StaticFiles(directory=str(TTS_DIR)), name="tts")

# ── 投票同步 ───────────────────────────────
# 投票窗口内累加计数，每次投票通过 Queue 推送 tally 到 SSE 流，
# 前端实时更新票数分布。窗口结束后取多数票。
_vote_tallies: dict[str, dict[str, int]] = {}
_vote_queues: dict[str, asyncio.Queue] = {}
# 无限流强化：sid → Session 引用（强化接口读取/修改轮回者状态）
_sessions: dict[str, "Session"] = {}
VOTE_WINDOW_SECONDS = 32  # 略长于前端 30s 倒计时，确保超时自动投的那一票也能被计入
LIVE_VOTE_SECONDS = 90     # 直播模式投票窗口（给观众打字+弹幕延迟留时间）

# CombatLoop 不解析 LLM 叙事文本判定伤害/撤退——纯叙事无法自然触发结局，
# 回合数超过此上限后强制收尾，避免战斗无限进行。
COMBAT_MAX_ROUNDS = 6

# EXIT 标记：AI 玩家模式下，KP 叙述末尾可附加 <<EXIT n>> 请求移动到第 n 个场景出口
_EXIT_MARKER_RE = re.compile(r"<<\s*EXIT[\s:]*(\d+)\s*>>", re.IGNORECASE)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health() -> dict:
    """健康检查端点，供反向代理/容器编排探活使用，不做任何鉴权。"""
    return {
        "status": "ok",
        "active_sessions": len(_vote_tallies),
    }


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
    """状态快照：无限流模式同时包含轮回者 + AI 队友（二者并存，不再互斥）。"""
    snap: dict = {}
    if session.state is not None and session.state.reincarnator is not None:
        rein = session.state.reincarnator
        snap["reincarnator"] = {
            "hp": rein.hp, "max_hp": rein.max_hp,
            "strength": rein.strength, "agility": rein.agility, "spirit": rein.spirit,
            "ap": rein.ap, "talents": list(rein.talents),
            "conditions": list(rein.conditions),
        }
    for inv in session.state.investigators:
        snap[inv.name] = {
            "hp": inv.hp, "max_hp": inv.max_hp,
            "san": inv.san, "max_san": inv.max_san,
            "luck": inv.luck,
            "conditions": list(inv.conditions),
            "inventory": list(inv.inventory),
        }
    return snap


_MAX_LLM_OUTPUT_CHARS = 6000  # 单次生成的硬上限，防止模型异常（卡在重复/不停止生成）导致内存无限增长


async def _chat_stream(client, system: str, user_msg: str,
                       temperature: float = 0.8, max_tokens: int = 2000):
    """流式聊天，yield token（状态机过滤 GS 标记，最小缓冲）。兼容 OllamaClient 和 RemoteClient。"""
    buf = ""
    in_marker = False
    total = 0
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
                            total += len(safe)
                    elif not _could_be_gs_prefix(buf):
                        yield buf
                        total += len(buf)
                        buf = ""
                if total >= _MAX_LLM_OUTPUT_CHARS:
                    log.warning("LLM 单次输出超过 %d 字符上限，提前截断", _MAX_LLM_OUTPUT_CHARS)
                    yield "\n……（内容过长已截断）"
                    return
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


async def _chat_generate(client, system: str, user_msg: str,
                         temperature: float = 0.8, max_tokens: int = 2000) -> str:
    """整段生成（内部仍走 _chat_stream，只是不逐 token yield），用于需要先拿到完整文本
    再做清洗/质量检查/可能重试的场景。"""
    parts = []
    async for token in _chat_stream(client, system, user_msg,
                                    temperature=temperature, max_tokens=max_tokens):
        parts.append(token)
    return "".join(parts)


async def _fake_stream(text: str, chunk_size: int = _FAKE_STREAM_CHUNK,
                       delay: float = _FAKE_STREAM_DELAY):
    """把已经生成完毕的整段文本按小块 + 微延迟 yield，模拟原有逐 token 打字机效果，
    前端 kp_token 事件的处理逻辑完全不用变。"""
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]
        await asyncio.sleep(delay)


def _consistency_world_view(session: "Session"):
    """把 GameState 的 npcs/investigators 包装成 consistency.check() 期望的鸭子类型对象。

    注：GameState.Npc 没有 wounds/hp 字段（这套 COC 系统不跟踪 NPC 生死），所以"死亡 NPC
    说话"这一半检查恒为假、安全空操作；"NPC 不在场景中却说话"这一半用 scene.npcs_here
    比对是真正有效的。
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        npcs=[SimpleNamespace(name=n.name, wounds=1) for n in session.state.npcs],
        characters=[SimpleNamespace(name=i.name) for i in session.state.investigators],
    )


def _check_kp_narration(text: str, *, user_msg: str = "", prev_answer: str = "",
                        scene=None, world_view=None,
                        roster_names: list[str] | None = None,
                        is_intro: bool = False) -> tuple[bool, str]:
    """检查一段已清洗的 KP 叙述是否需要重新生成一次。

    返回 (是否需要重试, 追加到重试 prompt 的中文修正指令)。命中优先级：开场白弱质量 >
    复读玩家的话 > 复述自己上一轮描述 > 与游戏状态矛盾（死亡/不在场 NPC 说话）。
    """
    if is_intro and roster_names is not None and intro_guard.is_weak_intro(text, roster_names):
        return True, intro_guard.INTRO_RETRY_NUDGE_ZH
    if user_msg and echo_guard.is_echo(text, user_msg):
        return True, echo_guard.ECHO_NUDGE_ZH
    if prev_answer and echo_guard.is_self_repetition(text, prev_answer):
        return True, echo_guard.REPEAT_NUDGE_ZH
    if world_view is not None:
        violations = consistency.check(text, world_view, scene)
        if violations:
            return True, consistency.retry_nudge_zh(violations)
    return False, ""


def _match_scene(text: str, *, min_score: float = 1.5) -> dict | None:
    """从叙述文本匹配场景图，低分不返回。"""
    try:
        sm = _scene_matcher()
        matches = sm.match(text, top_k=1, min_score=min_score)
        if matches:
            m = matches[0]
            return {"image": m.filename, "location": m.location, "mood": m.mood, "score": m.score}
    except Exception:
        log.debug("场景匹配失败: %r", text[:50], exc_info=True)
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
        log.warning("TTS 生成失败，本轮不带语音", exc_info=True)
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


_WORLD_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _modules_dir_for_world(world: str) -> Path:
    """按世界观选择模块池目录。world 为空或 coc 时用默认 COC 模块池，
    其他世界观用独立子目录（互不影响）。

    world 来自不受信任的请求参数，仅允许 [a-z0-9_]，否则回退默认池，
    避免通过 "../" 等构造路径穿越到 data/ 之外的目录（CWE-22）。
    """
    base = Path(__file__).resolve().parent.parent / "data"
    world = (world or "").strip().lower()
    if world in ("", "coc", "cof", "克苏鲁", "克苏鲁的呼唤"):
        return base / "modules"
    if not _WORLD_NAME_RE.match(world):
        log.warning("非法 world 参数已拒绝，回退默认 COC 模块池: %r", world)
        return base / "modules"
    return base / f"modules_{world}"


def _compose_max_depth(world: str) -> int:
    """按世界观返回组合深度上限。

    无限流副本链 = hub(1) + 副本入口(1) + 内部(1-2) + 通关(1) + 返回 hub，
    至少需要 5 层，默认 max_depth=3 会截断副本链导致无法通关。其他世界观
    保持原默认深度。
    """
    world = (world or "").strip().lower()
    if world == "infinite_flow" or world == "无限流":
        return 6
    return 3


def _scene_for_room(room_type: str) -> dict | None:
    """根据房间类型匹配场景图。"""
    try:
        sm = _scene_matcher()
        scene_type = _ROOM_SCENE_MAP.get(room_type, "")
        if scene_type:
            matches = sm.match_exact_scene_type(scene_type)
            if matches:
                m = matches[0]
                return {"image": f"/images/scenes/{m.filename}", "location": m.location, "mood": m.mood, "score": 1.0}
        # 回退：随机场景
        if sm._images:
            import random
            fname = random.choice(list(sm._images.keys()))
            tags = sm._images[fname]
            return {"image": f"/images/scenes/{fname}", "location": tags.get("location", ""),
                    "mood": (tags.get("mood", [""]) or [""])[0], "score": 0}
    except Exception:
        log.debug("房间场景匹配失败: room_type=%r", room_type, exc_info=True)
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
    # 无限流轮回者无 SAN——失败后果直接走 HP 伤害（50% 概率）
    if not hasattr(inv_state, "san"):
        if random.random() < 0.5:
            dmg = random.randint(1, 2)
            inv_state.take_damage(dmg)
            log.info("DICE_CONS: %s HP -%d (无限流无SAN)", inv_state.name, dmg)
            return {"type": "damage", "amount": dmg, "text": f"HP -{dmg}"}
        return None
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
    """生成调查员/轮回者状态摘要文本，供 CombatLoop 的战斗 prompt 使用。

    无限流模式输出轮回者三维属性 + HP + AP，并叠加 AI 队友的 HP/SAN（如有）；
    COC 模式走原逻辑。
    """
    parts = []
    if session.state is not None and session.state.reincarnator is not None:
        rein = session.state.reincarnator
        cond = f"（{', '.join(rein.conditions)}）" if rein.conditions else ""
        parts.append(f"轮回者 {rein.name} HP {rein.hp}/{rein.max_hp} "
                     f"力量 {rein.strength} 敏捷 {rein.agility} 精神 {rein.spirit} "
                     f"AP {rein.ap}{cond}")
    for inv in session.state.investigators:
        cond = f"（{', '.join(inv.conditions)}）" if inv.conditions else ""
        parts.append(f"{inv.name} HP {inv.hp}/{inv.max_hp} SAN {inv.san}/{inv.max_san}{cond}")
    return "；".join(parts)


def _build_teammate_prompt_data(session: Session, teammates: list[dict]) -> list[dict]:
    """构造队友行动提示所需的实时状态快照。"""
    prompt_data: list[dict] = []
    for inv in teammates:
        st = session.state.find_investigator(inv["name"])
        if st is None:
            prompt_data.append(inv)
            continue
        prompt_data.append({
            "name": inv["name"],
            "hp": st.hp,
            "max_hp": st.max_hp,
            "san": st.san,
            "max_san": st.max_san,
        })
    return prompt_data


def _resolve_outcome_ap(outcome_id: str, encounter: CombatEncounter | None) -> int:
    """计算副本结算 AP。

    若结局在 encounter.outcomes 中存在，严格使用模块声明值（含 0）。
    仅当拿不到结局配置时，回退旧默认：victory=3，其余=1。
    """
    if encounter is not None:
        oc = encounter.outcomes.get(str(outcome_id))
        if oc is not None:
            return oc.reward_ap
    return 3 if outcome_id == "victory" else 1


async def _vote_window(sid: str, timeout_seconds: int = VOTE_WINDOW_SECONDS):
    """投票窗口——推送实时票数变化，最后一次 yield 是 ('__result__', tally)。

    共享给常规投票和战斗投票两种场景使用。
    """
    _vote_tallies[sid] = {}
    vote_queue: asyncio.Queue = asyncio.Queue()
    _vote_queues[sid] = vote_queue
    deadline = asyncio.get_event_loop().time() + timeout_seconds
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
                       player_host: str = "http://localhost:11434",
                       force_pickup: bool = False,
                       vote_seconds: int = 32,
                       force_combat: bool = False,
                       world: str = "",
                       sid: str = "",
                       leader: str = "",
                       load_profile: bool = False):
    """SSE 事件流 — 完整的游戏循环。"""
    
    # ── 模块组合模式 ──────────────────────────
    adventure: Adventure | None = None
    composer = None  # ModuleComposer | None（rest/trap 机制查询用）
    if compose_modules:
        yield _sse("status", {"text": "组合模块剧情..."})
        modules_dir = _modules_dir_for_world(world)
        composer = ModuleComposer(modules_dir)
        composer.load_all()
        if not composer._modules:
            yield _sse("error", {"text": f"世界观 [{world or 'coc'}] 模块池为空：{modules_dir}"})
            return
        bundle = composer.compile(
            seed=seed,
            max_depth=_compose_max_depth(world),
            authored_only=((world or "").strip().lower() in ("infinite_flow", "无限流")),
        )
        adventure = bundle.adventure
        yield _sse("status", {"text": f"已组合 {len(bundle.module_ids)} 个模块, {len(adventure._scenes)} 个场景 (世界观: {world or 'coc'})"})

        # ── 强制战斗注入 ──
        if force_combat:
            import random as _rnd
            combat_mods = [m for m in composer._modules.values()
                          if m.meta.module_type == "combat" and m.encounter is not None]
            if combat_mods:
                cm = _rnd.choice(combat_mods)
                cs = _combat_to_scene(cm.meta.id, cm.encounter)
                adventure._scenes[cs.id] = cs
                adventure.start_scene = cs.id
                yield _sse("status", {"text": f"⚔️ 强制战斗：{cm.encounter.title} (难度 {cm.encounter.difficulty})"})
    
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
            log.warning("无法连接 Ollama host=%s 做模型预检，跳过校验", host, exc_info=True)
            available = []
        if available and kp_model not in available:
            yield _sse("error", {"text": f"KP模型 {kp_model} 不可用"})
            return

    kp_client = _make_client(host, kp_model, kp_api_key, timeout=300)

    # ── Session ───────────────────────────────
    sid = sid or f"web_{datetime.now().strftime('%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    old_dir = Path("data/sessions") / sid
    if old_dir.exists():
        shutil.rmtree(old_dir)
    session = Session(sid, auto_save_interval=0, max_context=8192)
    _sessions[sid] = session

    # ── 无限流：创建轮回者 ──────────────────────
    is_infinite_flow = _is_infinite_world(world)
    roster = _roster_for_world(world)  # 非无限流世界观各自的「1主控+2NPC队友」名单
    if is_infinite_flow:
        from trpg_agent.memory.game_state import Reincarnator, INITIAL_ALLOCATION_POINTS
        loaded = _load_reincarnator() if load_profile else None
        if loaded is not None:
            rein = loaded
            rein.hp = rein.max_hp  # 开局回满
            yield _sse("status", {"text": f"🌀 已继承轮回者 — 三维 {rein.strength}/{rein.agility}/{rein.spirit}，AP {rein.ap}，强化 {len(rein.talents)} 个"})
        else:
            rein = Reincarnator(name="轮回者", max_hp=12, hp=12,
                                strength=10, agility=10, spirit=10, ap=0)
            # 自由分配初始属性点：用户可后续通过强化面板分配
            rein.ap += INITIAL_ALLOCATION_POINTS
            yield _sse("status", {"text": f"🌀 轮回者已创建 — 三维属性 10/10/10，{INITIAL_ALLOCATION_POINTS} 点可分配"})
        if session.state is not None:
            session.state.reincarnator = rein

    for inv_data in (INFINITE_FLOW_TEAMMATES if is_infinite_flow else roster):
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

    rein_data = None
    if is_infinite_flow and session.state is not None and session.state.reincarnator is not None:
        rein = session.state.reincarnator
        rein_data = {
            **rein.to_dict(),
            "stats_text": (
                f"力量 {rein.strength} | 敏捷 {rein.agility} | 精神 {rein.spirit} "
                f"| HP {rein.hp}/{rein.max_hp} | AP {rein.ap}"
            ),
        }
    yield _sse("init", {
        "session_id": sid,
        "investigators": INFINITE_FLOW_TEAMMATES if is_infinite_flow else roster,
        "reincarnator": rein_data,
        "kp_model": kp_model, "player_model": player_model,
        "opening": opening_text, "room": rc, "mode": mode,
        "compose_modules": compose_modules,
        "vote_seconds": vote_seconds,
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
    # 注入世界观氛围指令（COC 恐怖 / 无限流副本）
    scene_context = start_scene.description if (adventure and (start_scene := adventure.get_scene(adventure.start_scene))) else OPENING
    if is_infinite_flow and session.state is not None and session.state.reincarnator is not None:
        rein_roster = [session.state.reincarnator.name] + [t["name"] for t in INFINITE_FLOW_TEAMMATES]
        coc_directive = (
            f"当前场景：{scene_context}\n"
            f"位置：{rc['name']} — {rc['desc']}\n"
            f"团队：{'、'.join(rein_roster)}（{rein_roster[0]} 为主控轮回者，其余为随行队友）\n"
            f"线索：{rc.get('clues', '暂无')}\n"
            f"⚠ 威胁：{rc.get('threats', '无')}\n\n"
            "【重要指令】\n"
            "1. 你是无限流副本主持人，营造危险与未知交织的副本氛围。\n"
            "2. 描述中必须包含感官细节：声音、气味、触感、光线。\n"
            "3. 如果房间有威胁，必须在叙述中暗示它——让玩家感到不安。\n"
            "4. 如果提到线索，让它显得诡异而非寻常。\n"
            "5. 叙述控制在3-5句，营造紧张氛围后把选择交还玩家。"
        )
        roster_names = rein_roster
    else:
        coc_directive = (
            f"当前场景：{scene_context}\n"
            f"位置：{rc['name']} — {rc['desc']}\n"
            f"角色：{', '.join(i['name'] for i in roster)}\n"
            f"线索：{rc.get('clues', '暂无')}\n"
            f"⚠ 威胁：{rc.get('threats', '无')}\n\n"
            "【重要指令】\n"
            f"1. 你是这个世界观（{world or 'coc'}）的主持人，营造符合设定基调的沉浸式氛围{'——人类渺小、真相可怖、理智侵蚀' if not world or world.lower() in ('coc', 'cthulhu') else ''}。\n"
            "2. 描述中必须包含感官细节：声音、气味、触感、光线。\n"
            "3. 如果房间有威胁，必须在叙述中暗示它——让玩家感到不安。\n"
            "4. 如果提到线索，让它显得诡异而非寻常。\n"
            "5. 叙述控制在3-5句，营造紧张氛围后把选择交还玩家。"
        )
        roster_names = [inv["name"] for inv in roster]
    yield _sse("kp_stream_start", {})
    raw_opening = await _chat_generate(kp_client, system_prompt, coc_directive,
                                       temperature=0.8, max_tokens=4000)
    opening_text = _sanitize(raw_opening) if raw_opening else ""
    needs_retry, nudge = (
        _check_kp_narration(opening_text, roster_names=roster_names, is_intro=True)
        if opening_text else (False, "")
    )
    if needs_retry:
        log.info("开场白质量守卫触发重试：%s", nudge)
        raw_retry = await _chat_generate(kp_client, system_prompt, coc_directive + "\n\n" + nudge,
                                         temperature=0.8, max_tokens=4000)
        if raw_retry:
            opening_text = _sanitize(raw_retry)
    if not opening_text:
        opening_text = f"你们站在{current_room.name}中。{current_room.description}"
    async for chunk in _fake_stream(opening_text):
        yield _sse("kp_token", {"text": chunk})
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
    if is_infinite_flow and session.state is not None and session.state.reincarnator is not None:
        # 无限流：主控固定为轮回者（无需投票选主控，也不轮转），2 个 AI 队友常驻辅助
        leader_name = session.state.reincarnator.name
        player_order = [leader_name]
        teammates = list(INFINITE_FLOW_TEAMMATES)
    else:
        player_order = [inv["name"] for inv in roster]
        # T3 队友系统：live 模式固定主控（leader）由投票驱动，其余为 AI 队友
        leader_name = leader or roster[0]["name"]
        if leader_name not in player_order:
            leader_name = roster[0]["name"]
        teammates = [inv for inv in roster if inv["name"] != leader_name]
    current_bgm = bgm_track
    current_scene = scene_info
    # ── 战斗编排器 ──
    rein = None
    if is_infinite_flow and session.state is not None:
        rein = session.state.reincarnator
    combat_orch = CombatOrchestrator(
        investigators_state=_investigators_state_text(session),
        melee_bonus=rein.melee_bonus() if rein else 0,
        dodge_bonus=rein.dodge_bonus() if rein else 0,
        spirit_resist_bonus=rein.spirit_resist_bonus() if rein else 0,
        reincarnator=rein,
    )

    _turn = 0  # 循环迭代计数（含战斗回合）
    _non_combat_turns = 0  # 非战斗回合计数
    while _non_combat_turns < turns:
        # live 模式：主控固定（投票驱动），不轮转
        speaker = leader_name if mode == "live" else player_order[_turn % len(player_order)]
        if is_infinite_flow and rein is not None and speaker == rein.name:
            inv_data = {"name": rein.name, "color": "#8a5fd6"}
            inv_state = rein
        else:
            inv_data = next(inv for inv in roster if inv["name"] == speaker)
            inv_state = session.state.find_investigator(speaker)
        rc = dmap.room_context()
        teammate_actions: dict[str, str] = {}  # T3：本轮 AI 队友行动（live 模式填充）
        _turn += 1

        # ── 战斗场景检测 ──
        in_combat = combat_orch.check_combat(adventure, session.state.scene_id)

        if in_combat:
            # 首次进入：应用队伍状态缩放
            if combat_orch.combat_loop is not None and combat_orch.combat_loop.current_round == 0:
                invs = session.state.investigators
                if invs:
                    avg_hp = sum(i.hp / max(i.max_hp, 1) for i in invs) / len(invs)
                    combat_orch.combat_loop._state.encounter.apply_scaling(len(invs), party_hp_ratio=avg_hp)
                yield _sse("status", {"text": f"⚔️ 战斗开始：{combat_orch.combat_loop._state.encounter.title}"})

            # ── 进场叙事 + 选项生成 ──
            enter_sys, enter_usr = combat_orch.prepare_enter()
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
            round_state = combat_orch.complete_enter(enter_output)
            audio_url = await _speak(round_state.opening_narration[:500])
            yield _sse("kp_stream_end", {
                "state": _state_snapshot(session),
                "scene": None,
                "audio_url": audio_url,
                "bgm_track": None,
            })

            # ── 投票 ──
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
            choice = max(vote_options.keys(), key=lambda k: tally.get(k, 0)) if tally else next(iter(vote_options.keys()), "a")

            # ── 机制 + 叙事结算 ──
            mech_result = combat_orch.submit_and_resolve_mechanics(choice)
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            yield _sse("player_token", {"text": f"（全员选择了「{vote_options[choice]}」）", "speaker": speaker})
            yield _sse("player_stream_end", {"speaker": speaker})
            # ── 应用伤害到调查员状态（按人数精确分摊，总和不超过原始伤害）──
            if mech_result.damage_to_investigators:
                if is_infinite_flow and session.state is not None and session.state.reincarnator is not None:
                    rein = session.state.reincarnator
                    rein.take_damage(mech_result.damage_to_investigators)
                else:
                    n = len(roster)
                    base_dmg, remainder = divmod(mech_result.damage_to_investigators, n)
                    for i, invd in enumerate(roster):
                        dmg = base_dmg + (1 if i < remainder else 0)
                        if dmg <= 0:
                            continue
                        st = session.state.find_investigator(invd["name"])
                        if st:
                            st.take_damage(dmg)
            if mech_result.san_loss and not is_infinite_flow:
                inv_state.san = max(0, inv_state.san - mech_result.san_loss)
            # 推送掷骰/伤害结果到前端（speaker/skill 供前端日志/骰子叠加层展示）
            yield _sse("dice_roll", {
                "text": mech_result.summary,
                "speaker": speaker,
                "skill": mech_result.skill or "",
                "success": mech_result.success,
                "damage_to_enemies": mech_result.damage_to_enemies,
                "damage_to_investigators": mech_result.damage_to_investigators,
                "san_loss": mech_result.san_loss,
            })
            await asyncio.sleep(0.2)
            # BOSS 阶段事件推送（狂暴/召唤等，独立 status 让前端醒目展示）
            for ph in (getattr(mech_result, "phase_events", None) or []):
                yield _sse("status", {"text": f"⚠️ {ph.get('name', '阶段变化')}——{ph.get('behavior', '')}"})
                await asyncio.sleep(0.4)

            res_sys, res_usr = combat_orch.prepare_resolve()
            yield _sse("kp_stream_start", {})
            resolution_output = ""
            async for token in _chat_stream(kp_client, res_sys, res_usr,
                                            temperature=0.8, max_tokens=1500):
                resolution_output += token
                yield _sse("kp_token", {"text": token})
            if not resolution_output:
                resolution_output = f"（{mech_result.summary}）"

            result = combat_orch.complete_turn(resolution_output)
            outcome = result.outcome

            # 极端兜底
            if outcome is None:
                fallback = combat_orch.force_end_if_needed(COMBAT_MAX_ROUNDS)
                if fallback:
                    outcome = fallback
                    resolution_output += "\n\n（战斗持续过久，局势再也无法维持……）"
                    yield _sse("kp_token", {"text": "\n\n" + combat_orch.combat_loop.end_summary()})

            if outcome is not None:
                yield _sse("kp_token", {"text": "\n\n" + combat_orch.combat_loop.end_summary()})
                resolution_output += "\n\n" + combat_orch.combat_loop.end_summary()

            session.record_turn(result.action, resolution_output, speaker=speaker)
            last_narration = resolution_output

            if outcome is not None:
                summary = combat_orch.combat_summary()
                if summary:
                    session.record_combat(summary)

            # ── 无限流：副本通关结算 AP ──────────
            # 优先读战斗 encounter 结局声明的 reward_ap（模块作者可配置），
            # 读不到或为 0 时回退旧硬编码数值（victory 3 / defeat/flee 1）。
            ap_gained = 0
            if is_infinite_flow and outcome is not None and session.state is not None and session.state.reincarnator is not None:
                rein = session.state.reincarnator
                encounter = None
                if combat_orch.combat_loop is not None and combat_orch.combat_loop._state is not None:
                    encounter = combat_orch.combat_loop._state.encounter
                ap_gained = _resolve_outcome_ap(str(outcome), encounter)
                if ap_gained:
                    rein.ap += ap_gained
                    yield _sse("status", {"text": f"💰 副本结算：获得 {ap_gained} 强化点（AP），当前 {rein.ap} 点"})
                # T6：副本结束（任一结局）存档轮回者
                _save_reincarnator(rein)

            audio_url = await _speak(resolution_output[:500])

            # ── 战斗结束跳转 ──
            moved_scene = None
            if outcome is not None:
                scene = adventure.get_scene(session.state.scene_id) if adventure else None
                if scene:
                    trans = combat_orch.scene_transition_info(scene)
                    if trans:
                        _mid, target_id = trans
                        moved_scene = session.move_to_scene(target_id, adventure)
                        if moved_scene is not None:
                            moved_scene, _trans_meta = _auto_advance_transitions(session, adventure, moved_scene)
                combat_orch.reset()

            # ── 场景切换 / 收尾 ──
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
                # 模块机制：rest/trap 效果（战斗跳转进入的新场景）
                for _ef in _module_scene_effects(composer, adventure, session.state.scene_id, session, is_infinite_flow):
                    yield _sse("status", {"text": _ef})
                    await asyncio.sleep(0.3)

            yield _sse("kp_stream_end", {
                "state": _state_snapshot(session),
                "scene": current_scene if scene_changed else None,
                "audio_url": audio_url,
                "bgm_track": new_bgm if bgm_changed else None,
                "room_change": room_change,
                "room": rc,
            })
            await asyncio.sleep(0.3)
            continue  # 战斗回合自成一体

        # ── 玩家行动 ───────────────────────────
        moved_scene = None  # 投票驱动的模块场景切换结果（仅模块模式下可能非 None）
        trans_meta = None   # 过渡元数据链（供 KP 过渡指令构建）
        if mode == "ai":
            # ── AI 模式：本地 LLM 扮演调查员 ────
            # 模块交互优先：puzzle/social/choice/interaction 场景，AI 自动选一个并结算
            ai_interactions = _module_interaction_options(
                composer, adventure, session.state.scene_id, session)
            if ai_interactions:
                ai_choice = await _ai_pick_option(
                    player_host, player_model, {l: v["text"] for l, v in ai_interactions.items()},
                    last_narration, speaker, kp_api_key)
                if ai_choice not in ai_interactions:
                    ai_choice = next(iter(ai_interactions))
                sel = ai_interactions[ai_choice]
                yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
                yield _sse("player_token", {"text": f"（{speaker} 选择：{sel['text']}）", "speaker": speaker})
                yield _sse("player_stream_end", {"speaker": speaker})
                await asyncio.sleep(0.2)
                for _ef in _module_interaction_resolve(sel, session, is_infinite_flow):
                    yield _sse("status", {"text": _ef})
                    await asyncio.sleep(0.3)
                continue  # 交互回合不消耗移动/检定流程
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            action = ""
            # force_pickup: 首轮强制拾取房间物品
            if force_pickup and _non_combat_turns == 0:
                room_items = dmap.current_room.items if dmap.current_room else []
                if room_items:
                    action = f"{speaker}捡起{room_items[0]}"
                    yield _sse("player_token", {"text": action, "speaker": speaker})
                else:
                    action = f"（{speaker} 谨慎地观察四周）"
                    yield _sse("player_token", {"text": action, "speaker": speaker})
            else:
                async for token in _ai_player_stream(player_host, player_model, inv_data, inv_state,
                                                     rc, last_narration, speaker,
                                                     is_infinite_flow=is_infinite_flow,
                                                     api_key=kp_api_key):
                    action += token
                    yield _sse("player_token", {"text": token, "speaker": speaker})
                if not action.strip():
                    action = f"（{speaker} 谨慎地观察四周）"
                    yield _sse("player_token", {"text": action, "speaker": speaker})
            yield _sse("player_stream_end", {"speaker": speaker})
            await asyncio.sleep(0.2)
        else:
            # ── 人类 / 直播模式：展示选项，等待投票 ──
            is_live = mode == "live"
            vote_timeout = vote_seconds if is_live else VOTE_WINDOW_SECONDS
            vote_targets: dict[str, str] = {}
            opp_actions: dict[str, str] = {}  # 机会选项（无 target，选中走检定）
            module_interactions: dict[str, dict] | None = None  # 模块交互选项（puzzle/social/choice/interaction）
            if adventure is not None:
                # 模块交互优先：puzzle/social/choice/interaction 场景先用作者定义的交互选项
                module_interactions = _module_interaction_options(
                    composer, adventure, session.state.scene_id, session)
                if module_interactions:
                    vote_options = {l: v["text"] for l, v in module_interactions.items()}
                    opp_actions = {l: l for l in module_interactions}  # 占位：选中后单独结算
                else:
                    # 模块模式：投票选项 = 当前场景的真实出口，投票结果将驱动场景切换
                    exits = adventure.scene_exits(
                        session.state.scene_id, resolved_ids=session.state.resolved_elements,
                    )
                    letters = ["a", "b", "c"]
                    vote_options = {}
                    for letter, exit_view in zip(letters, exits):
                        vote_options[letter] = exit_view.label
                        vote_targets[letter] = exit_view.target_id
                    # 出口不足 3 个时，用场景 opportunities（作者写的剧情贴合互动）补齐，
                    # 而非泛用填充词；选中机会选项会作为玩家行动进入检定流程
                    if len(vote_options) < 3:
                        scene_obj = adventure.get_scene(session.state.scene_id)
                        opps = [o.text for o in (scene_obj.opportunities if scene_obj else []) or [] if o.text]
                        for letter, opp in zip(letters[len(vote_options):], opps):
                            vote_options[letter] = opp
                            opp_actions[letter] = opp
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

            # T5：无限流 hub 场景附加已通关副本标记（供前端卡片显示 ✓）
            cleared_dungeons: list[str] = []
            if is_infinite_flow and adventure is not None and session.state is not None:
                hub_scene = adventure.get_scene(session.state.scene_id)
                if hub_scene is not None and "主神空间" in (hub_scene.title or ""):
                    for clue, label in (("dungeon_clear_jy", "咒怨"),
                                        ("dungeon_clear_rs", "生化"),
                                        ("dungeon_clear_xt", "修仙")):
                        if clue in session.state.resolved_elements:
                            cleared_dungeons.append(label)

            yield _sse("vote", {
                "options": vote_options,
                "session_id": sid,
                "speaker": speaker,
                "vote_seconds": vote_timeout,
                "cleared_dungeons": cleared_dungeons,
            })

            # T3：投票窗口期间并行生成 AI 队友行动（隐藏延迟，不拖长单轮）
            teammate_task = None
            if is_live:
                teammate_prompt_data = _build_teammate_prompt_data(session, teammates)
                teammate_task = asyncio.create_task(
                    _ai_teammates_action(player_host, player_model, teammate_prompt_data, rc, last_narration, "", kp_api_key)
                )

            # 投票窗口：Queue 驱动循环，每次投票推送 tally 给前端，窗口结束后取多数票
            tally: dict[str, int] = {}
            async for evt_name, evt_data in _vote_window(sid, vote_timeout):
                if evt_name == "__result__":
                    tally = evt_data
                else:
                    yield _sse(evt_name, evt_data)
            if tally:
                choice = max(vote_options.keys(), key=lambda k: tally.get(k, 0))
            elif is_live:
                # 直播模式无人投票 → AI 接手选择
                choice = await _ai_pick_option(player_host, player_model, vote_options, last_narration, speaker, kp_api_key)
                yield _sse("ai_pick", {"choice": choice, "label": vote_options.get(choice, "")})
            else:
                choice = "a"

            # 投票结束：取回队友行动（投票窗口内已完成，失败则空 dict 兜底）
            if teammate_task is not None:
                teammate_actions = await teammate_task

            # 投票结果驱动模块场景切换——只有选中真实出口时才会真正移动
            target_scene_id = vote_targets.get(choice)
            # 模块交互结算：选中 puzzle/social/choice/interaction 选项时立即结算，不移动场景
            if module_interactions and choice in module_interactions:
                for _ef in _module_interaction_resolve(module_interactions[choice], session, is_infinite_flow):
                    yield _sse("status", {"text": _ef})
                    await asyncio.sleep(0.3)

            if adventure is not None and target_scene_id:
                moved_scene = session.move_to_scene(target_scene_id, adventure)
                if moved_scene is not None:
                    moved_scene, trans_meta = _auto_advance_transitions(session, adventure, moved_scene)
                # 模块机制：rest/trap 效果（投票移动进入的新场景）
                if moved_scene is not None and session.state is not None:
                    for _ef in _module_scene_effects(composer, adventure, session.state.scene_id, session, is_infinite_flow):
                        yield _sse("status", {"text": _ef})
                        await asyncio.sleep(0.3)

            if module_interactions and choice in module_interactions:
                # 模块交互选项：用选项文字作为玩家行动（不进场景移动）
                action = module_interactions[choice].get("text", "")
            elif choice in opp_actions:
                # 机会选项：直接作为玩家行动，进入检定/拾取流程（有实际后果）
                action = opp_actions[choice]
            else:
                action = f"（{speaker} 选择了「{vote_options.get(choice, '')}」）"
            yield _sse("player_stream_start", {"speaker": speaker, "color": inv_data["color"]})
            yield _sse("player_token", {"text": action, "speaker": speaker})
            if teammate_actions:
                for _tname, _tact in teammate_actions.items():
                    yield _sse("player_token", {"text": f"（{_tname}：{_tact}）", "speaker": _tname})
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
            log.warning("检定解析/结算失败，本轮跳过检定", exc_info=True)
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
                TYPE_LABELS = {
                    "combat": "战斗", "story": "剧情",
                    "investigation": "调查", "exploration": "探索",
                    "social": "社交", "horror": "恐怖", "rest": "休整",
                }
                from_type_label = TYPE_LABELS.get(tm.get("from_type", ""), tm.get("from_type", ""))
                to_type_label = TYPE_LABELS.get(tm.get("to_type", ""), tm.get("to_type", ""))
                from_type = tm.get("from_type", "")
                to_type = tm.get("to_type", "")

                trans_parts = [
                    "[场景过渡]",
                    f"队伍离开了「{tm['from_title']}」（{from_type_label}场景），现在来到了「{tm['to_title']}」（{to_type_label}场景）。",
                ]
                if tm.get("to_desc"):
                    trans_parts.append(f"抵达后的景象：{tm['to_desc']}")

                # 类型切换提示——不同方向的切换给出不同的叙事指引
                if from_type and to_type and from_type != to_type:
                    hints = _transition_hint(from_type, to_type)
                    if hints:
                        trans_parts.append(hints)

                trans_parts.append(
                    "请用2-3句话叙述从离开到抵达的自然过渡。不要使用模板化句式。"
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
        # T3：队友行动并入 KP 叙述上下文（live 模式）
        if teammate_actions:
            team_text = "；".join(f"{name} {act}" for name, act in teammate_actions.items())
            context_parts.append(f"[队友行动] {team_text}")
        kp_user = "\n\n".join(context_parts) + "\n\n请叙述结果："

        yield _sse("kp_stream_start", {})
        raw_narration = await _chat_generate(kp_client, system_prompt, kp_user,
                                             temperature=0.8, max_tokens=4000)
        narration = _sanitize(raw_narration) if raw_narration else ""
        if narration:
            scene_for_check = adventure.get_scene(session.state.scene_id) if adventure else None
            needs_retry, nudge = _check_kp_narration(
                narration, user_msg=kp_user, prev_answer=last_narration,
                scene=scene_for_check, world_view=_consistency_world_view(session),
            )
            if needs_retry:
                log.info("KP 叙述质量守卫触发重试：%s", nudge)
                raw_retry = await _chat_generate(kp_client, system_prompt, kp_user + "\n\n" + nudge,
                                                 temperature=0.8, max_tokens=4000)
                if raw_retry:
                    narration = _sanitize(raw_retry)
        if not narration:
            narration = "（KP 沉思……）"

        # 模块模式 + AI 玩家：解析 KP 叙述里的 <<EXIT n>>，驱动场景切换
        # 如果 KP 没输出 <<EXIT n>>，自动推进到下一个模块场景（保持背景图持续更新）
        if adventure is not None and mode == "ai":
            m = _EXIT_MARKER_RE.search(narration)
            current_exits = adventure.scene_exits(
                session.state.scene_id, resolved_ids=session.state.resolved_elements,
            )
            exit_choice = None
            if m:
                narration = _EXIT_MARKER_RE.sub("", narration).rstrip()
                exit_choice = int(m.group(1))
            elif current_exits and _non_combat_turns > 0:
                # KP 没给出口标记 → 自动选择（单出口直接走，多出口随机）
                import random as _random
                exit_choice = 1 if len(current_exits) == 1 else _random.randint(1, len(current_exits))
            if exit_choice is not None and 1 <= exit_choice <= len(current_exits):
                moved_scene = session.move_to_scene(
                    current_exits[exit_choice - 1].target_id, adventure,
                )
                if moved_scene is not None:
                    moved_scene, trans_meta = _auto_advance_transitions(session, adventure, moved_scene)

        async for chunk in _fake_stream(narration):
            yield _sse("kp_token", {"text": chunk})

        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration
        _non_combat_turns += 1  # 战斗回合不计入总回合数

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
                for inv_data_i in (INFINITE_FLOW_TEAMMATES if is_infinite_flow else roster):
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

        # 始终推送当前场景图，让前端自行去重
        yield _sse("kp_stream_end", {
            "state": _state_snapshot(session),
            "scene": current_scene,
            "audio_url": audio_url,
            "bgm_track": new_bgm if bgm_changed else None,
            "room_change": room_change,
            "room": rc,
        })

        await asyncio.sleep(0.3)

    yield _sse("done", {"summary": session.state.scene_summary()})


def _save_reincarnator(rein) -> None:
    """T6：轮回者存档——副本结束（任一结局）时写入 profiles/。"""
    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        path = PROFILE_DIR / f"{rein.name}.json"
        path.write_text(json.dumps(rein.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("轮回者已存档: %s (AP=%s)", path, rein.ap)
    except Exception:
        log.exception("轮回者存档失败")


def _load_reincarnator(name: str = "轮回者"):
    """T6：读轮回者存档，无存档返回 None。"""
    from trpg_agent.memory.game_state import Reincarnator
    try:
        path = PROFILE_DIR / f"{name}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Reincarnator.from_dict(data)
    except Exception:
        log.exception("轮回者读档失败")
        return None


def _is_infinite_world(world: str) -> bool:
    return (world or "").strip().lower() in ("infinite_flow", "无限流")


async def _ai_teammates_action(player_host: str, player_model: str,
                               teammates: list[dict], rc: dict,
                               last_narration: str, leader_action: str,
                               api_key: str = "") -> dict[str, str]:
    """生成 AI 队友行动（合并为一次请求），返回 {名字: 行动}。

    设计（docs/infinite-flow-v2-design.md §5.2/5.3）：
    - 一次 prompt 生成全部队友行动，控制延迟与成本
    - 队友只贡献叙事/检定，不触发场景移动
    - 失败兜底：任何异常返回空 dict，调用方走默认"谨慎观察"
    """
    if not teammates:
        return {}
    names = [inv["name"] for inv in teammates]
    team_line = "\n".join(f"- {inv['name']}（HP {inv['hp']}/{inv['max_hp']} SAN {inv['san']}/{inv['max_san']}）" for inv in teammates)
    system = (
        "你是跑团中的 AI 队友（非玩家主控角色）。你的作用是让队伍看起来像活人——"
        "对当前场景和主控角色的行动做出自然反应，每轮每人只说 1-2 句话。\n"
        "规则：不要替主控角色做决定、不要主动移动场景、不要长篇大论。\n"
        f"当前队伍队友：\n{team_line}\n"
        "输出格式：每个队友单独一行，以「名字：」开头。例如：\n"
        "林晓：我蹲下来查看地上的血迹。\n"
        "王刚：我挡在门口，警惕地盯着走廊尽头。"
    )
    msg = (
        f"主持人叙述：{last_narration[:400]}\n"
        f"主控角色行动：{leader_action[:200]}\n\n"
        f"当前房间：{rc.get('name', '')} — {rc.get('desc', '')}\n"
        f"威胁：{rc.get('threats', '')}\n\n"
        "请生成两个队友各自的行动："
    )
    try:
        client = _make_client(player_host, player_model, api_key, num_ctx=4096, timeout=120)
        try:
            raw = await client.chat(
                system, [{"role": "user", "content": msg}],
                options={"temperature": 0.9},
            )
        finally:
            await client.aclose()
    except Exception:
        log.warning("AI 队友行动生成失败，回退为静默", exc_info=True)
        return {}

    return _parse_teammates_action(raw or "", names)


def _parse_teammates_action(raw: str, names: list[str]) -> dict[str, str]:
    """解析 LLM 返回的队友行动文本为 {名字: 行动}。

    容错规则：
    - 只保留名字前缀完全匹配的行（防止模型输出无关内容）
    - 一行内名字后跟「：」或「:」，取冒号后内容
    - 名字没出现 / 内容为空 → 跳过（调用方兜底）
    """
    result: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for name in names:
            for sep in ("：", ":"):
                if line.startswith(name + sep):
                    action = line[len(name) + len(sep):].strip()
                    if action:
                        result[name] = action
                    break
            else:
                continue
            break
    return result


async def _ai_player_stream(player_host: str, player_model: str,
                           inv_data: dict, inv_state, rc: dict,
                           last_narration: str, speaker: str,
                           is_infinite_flow: bool = False, api_key: str = ""):
    """流式生成 AI 玩家行动，逐 token yield。

    inv_state 在无限流模式下是 Reincarnator（无 skills/san/inventory 属性），
    因此需要按 is_infinite_flow 分别构造 prompt，避免 AttributeError。
    """
    if is_infinite_flow:
        talents_str = "、".join(inv_state.talents) if inv_state.talents else "无"
        player_system = (
            f"你是 {inv_data['name']}，无限流副本中的轮回者。\n"
            f"HP:{inv_state.hp}/{inv_state.max_hp} "
            f"力量:{inv_state.strength} 敏捷:{inv_state.agility} 精神:{inv_state.spirit} "
            f"AP:{inv_state.ap}\n"
            f"天赋：{talents_str}\n\n"
            f"当前房间：{rc['name']} — {rc['desc']}\n"
            f"出口：{rc['exits']}\n"
            f"⚠ 房间里有这些东西可以拿：{rc['items']}\n"
            f"威胁：{rc['threats']}\n\n"
            "用第一人称描述行动，1-2句话。优先探索房间里的物品（说出物品名），"
            "其次是调查环境或应对威胁。不要替队友说话。"
        )
    else:
        skills_str = json.dumps(inv_state.skills, ensure_ascii=False)
        items_str = ", ".join(inv_state.inventory) if inv_state.inventory else "无"
        player_system = (
            f"你是 {inv_data['name']}，正在参与一场跑团游戏中的角色。\n"
            f"HP:{inv_state.hp}/{inv_state.max_hp} SAN:{inv_state.san}/{inv_state.max_san}\n"
            f"技能：{skills_str}  已有物品：{items_str}\n\n"
            f"当前房间：{rc['name']} — {rc['desc']}\n"
            f"出口：{rc['exits']}\n"
            f"⚠ 房间里有这些东西可以拿：{rc['items']}\n"
            f"威胁：{rc['threats']}\n\n"
            "用第一人称描述行动，1-2句话。优先探索房间里的物品（说出物品名），"
            "其次是调查环境或应对威胁。不要替其他角色说话。"
        )
    player_msg = f"主持人叙述：{last_narration[:600]}\n\n{inv_data['name']}的行动："
    player_client = _make_client(player_host, player_model, api_key, num_ctx=4096, timeout=120)
    try:
        async for token in player_client.chat_stream(
            player_system, [{"role": "user", "content": player_msg}],
            options={"temperature": 0.9},
        ):
            yield token
    except Exception:
        log.warning("AI 玩家流式生成失败", exc_info=True)
    finally:
        await player_client.aclose()


async def _ai_pick_option(player_host: str, player_model: str,
                          vote_options: dict[str, str],
                          last_narration: str, speaker: str,
                          api_key: str = "") -> str:
    """AI 从投票选项中选一个，返回单字母 'a'/'b'/'c'。"""
    options_text = "\n".join(f"{k}. {v}" for k, v in vote_options.items())
    system = (
        f"你是 {speaker}，正在参与一场跑团游戏。\n"
        "你必须从以下选项中选择一个行动。只回复一个字母(a/b/c)，不要任何解释。"
    )
    msg = f"KP叙述：{last_narration[:500]}\n\n可选行动：\n{options_text}\n\n你的选择(a/b/c)："
    try:
        client = _make_client(player_host, player_model, api_key, num_ctx=2048, timeout=30)
        response = await client.chat(
            system, [{"role": "user", "content": msg}],
            options={"temperature": 0.5, "num_predict": 5},
        )
        await client.aclose()
        text = response.strip().lower()
        for letter in ["a", "b", "c"]:
            if letter in text:
                return letter
    except Exception:
        log.warning("AI 投票选项挑选失败，回退为默认选项 a", exc_info=True)
    return "a"


def _module_scene_effects(composer, adventure, scene_id, session, is_infinite_flow: bool) -> list[str]:
    """进入新场景时按模块类型触发机制（rest 恢复 / trap 检定）。返回播报文本列表。"""
    if composer is None or session is None or session.state is None:
        return []
    module_id = (scene_id or "").split("::")[0]
    mod = composer._modules.get(module_id)
    if mod is None:
        return []
    meta = mod.meta
    texts: list[str] = []
    if meta.module_type == "rest" and meta.rest:
        hp = int(meta.rest.get("hp_recover", 0) or 0)
        san = int(meta.rest.get("san_recover", 0) or 0)
        rein = session.state.reincarnator
        if is_infinite_flow and rein is not None:
            total = 0
            if rein.hp < rein.max_hp:
                total = min(rein.max_hp - rein.hp, hp + san)
                rein.hp += total
            texts.append(f"🍖 在「{meta.title}」稍作休整，{rein.name} 恢复了 {total} 点生命（{rein.hp}/{rein.max_hp}）")
        else:
            for inv in session.state.investigators:
                if hp and inv.hp < getattr(inv, "max_hp", inv.hp):
                    inv.hp = min(getattr(inv, "max_hp", inv.hp), inv.hp + hp)
                if san and inv.san < getattr(inv, "max_san", inv.san):
                    inv.san = min(getattr(inv, "max_san", inv.san), inv.san + san)
            texts.append(f"🍖 在「{meta.title}」稍作休整，全员恢复 {hp} HP / {san} SAN")
    elif meta.module_type == "trap" and meta.trap:
        trap = meta.trap
        diff = int(trap.get("difficulty", 12) or 12)
        hp_loss = int(trap.get("hp_loss", 0) or 0)
        san_loss = int(trap.get("san_loss", 0) or 0)
        clue = str(trap.get("success_clue", "") or "")
        roll = random.randint(1, 20)
        success = roll <= diff
        rein = session.state.reincarnator
        if success:
            if clue:
                session.state.resolved_elements.add(clue)
            texts.append(f"🎲 「{meta.title}」检定成功（d20={roll}≤{diff}）！你们识破了危险" + (f"，发现线索：{clue}" if clue else ""))
        else:
            total = hp_loss + san_loss
            if is_infinite_flow and rein is not None:
                rein.hp = max(1, rein.hp - total)
                texts.append(f"💥 「{meta.title}」检定失败（d20={roll}>{diff}），{rein.name} 损失 {total} 点生命（{rein.hp}/{rein.max_hp}）")
            else:
                if session.state.investigators:
                    inv = session.state.investigators[0]
                    inv.hp = max(1, inv.hp - total)
                texts.append(f"💥 「{meta.title}」检定失败（d20={roll}>{diff}），损失 {total} 点生命")
    return texts


def _module_interaction_options(composer, adventure, scene_id, session) -> dict[str, dict] | None:
    """生成当前场景所属模块的交互选项（puzzle/social/choice/interaction）。

    返回 {letter: {kind, text, ...payload}}——若模块无交互或已结算返回 None。
    已结算标记：resolved_elements 含 f"{module_id}_interacted"。
    """
    if composer is None or session is None or session.state is None:
        return None
    module_id = (scene_id or "").split("::")[0]
    mod = composer._modules.get(module_id)
    if mod is None:
        return None
    meta = mod.meta
    mark = f"{module_id}_interacted"
    if mark in session.state.resolved_elements:
        return None
    letters = ["a", "b", "c"]
    if meta.module_type == "puzzle" and meta.puzzle:
        opts = meta.puzzle.get("options") or []
        out = {}
        for i, o in enumerate(opts[:3]):
            if not isinstance(o, dict) or not o.get("text"):
                continue
            out[letters[i]] = {
                "kind": "puzzle",
                "text": str(o["text"]),
                "correct": bool(o.get("correct", False)),
                "clue": str(o.get("clue", "") or ""),
                "penalty": int(o.get("penalty", 0) or 0),
                "module_id": module_id,
            }
        return out or None
    if meta.module_type == "social" and meta.social:
        opts = meta.social.get("responses") or []
        out = {}
        for i, o in enumerate(opts[:3]):
            if not isinstance(o, dict) or not o.get("text"):
                continue
            out[letters[i]] = {
                "kind": "social",
                "text": str(o["text"]),
                "effect_type": str(o.get("effect_type", "") or ""),
                "effect_value": int(o.get("effect_value", 0) or 0),
                "clue": str(o.get("success_clue", "") or ""),
                "fail_text": str(o.get("fail_text", "") or ""),
                "module_id": module_id,
            }
        return out or None
    if meta.module_type == "choice" and meta.choice:
        opts = meta.choice.get("options") or []
        out = {}
        for i, o in enumerate(opts[:3]):
            if not isinstance(o, dict) or not o.get("text"):
                continue
            out[letters[i]] = {
                "kind": "choice",
                "text": str(o["text"]),
                "clue": str(o.get("clue", "") or ""),
                "hp_cost": int(o.get("hp_cost", 0) or 0),
                "san_cost": int(o.get("san_cost", 0) or 0),
                "reward_text": str(o.get("reward_text", "") or ""),
                "module_id": module_id,
            }
        return out or None
    if meta.interaction:
        # interaction 是任意类型模块的轻量互动增强（story/rest 等）——
        # 进场景先结算自动效果（如 rest 回血），再给互动选项
        opts = meta.interaction.get("options") or []
        out = {}
        for i, o in enumerate(opts[:3]):
            if not isinstance(o, dict) or not o.get("text"):
                continue
            out[letters[i]] = {
                "kind": "interaction",
                "text": str(o["text"]),
                "clue": str(o.get("clue", "") or ""),
                "hp_cost": int(o.get("hp_cost", 0) or 0),
                "san_cost": int(o.get("san_cost", 0) or 0),
                "result_text": str(o.get("result_text", "") or ""),
                "module_id": module_id,
            }
        return out or None
    return None


def _module_interaction_resolve(opt: dict, session, is_infinite_flow: bool) -> list[str]:
    """结算一次模块交互选项（puzzle/social/choice/interaction）。返回播报文本。"""
    if session is None or session.state is None:
        return []
    texts: list[str] = []
    kind = opt.get("kind", "")
    module_id = opt.get("module_id", "")
    rein = session.state.reincarnator
    mark = f"{module_id}_interacted"

    if kind == "puzzle":
        if opt.get("correct"):
            clue = opt.get("clue", "")
            if clue:
                session.state.resolved_elements.add(clue)
            texts.append(f"🧩 谜题破解！你们解开了谜底" + (f"，获得线索：{clue}" if clue else ""))
        else:
            penalty = opt.get("penalty", 0) or 0
            if penalty:
                if is_infinite_flow and rein is not None:
                    rein.hp = max(1, rein.hp - penalty)
                    texts.append(f"💥 解谜失败，{rein.name} 损失 {penalty} 点生命（{rein.hp}/{rein.max_hp}）")
                else:
                    if session.state.investigators:
                        inv = session.state.investigators[0]
                        inv.hp = max(1, inv.hp - penalty)
                    texts.append(f"💥 解谜失败，损失 {penalty} 点生命")
            else:
                texts.append(f"🧩 谜底不对——看来需要再想想。")
        session.state.resolved_elements.add(mark)
        return texts

    if kind == "social":
        eff = opt.get("effect_type", "")
        val = opt.get("effect_value", 0) or 0
        clue = opt.get("clue", "")
        if eff == "clue" and clue:
            session.state.resolved_elements.add(clue)
            texts.append(f"🗣️ 对方被打动，说出了关键情报：{clue}")
        elif eff == "hp" and val:
            if is_infinite_flow and rein is not None:
                rein.heal(val)
                texts.append(f"🗣️ 对方提供了帮助，{rein.name} 恢复 {val} 点生命（{rein.hp}/{rein.max_hp}）")
            else:
                for inv in session.state.investigators:
                    inv.hp = min(getattr(inv, "max_hp", inv.hp), inv.hp + val)
                texts.append(f"🗣️ 对方提供了帮助，全员恢复 {val} 点生命")
        elif eff == "san" and val:
            for inv in session.state.investigators:
                inv.san = min(getattr(inv, "max_san", inv.san), inv.san + val)
            texts.append(f"🗣️ 一番交谈让人心安，全员恢复 {val} 点理智")
        else:
            texts.append(f"🗣️ 对方沉默了片刻，似乎有所保留。")
        session.state.resolved_elements.add(mark)
        return texts

    if kind == "choice":
        clue = opt.get("clue", "")
        hp_cost = opt.get("hp_cost", 0) or 0
        san_cost = opt.get("san_cost", 0) or 0
        reward = opt.get("reward_text", "") or ""
        if clue:
            session.state.resolved_elements.add(clue)
        if hp_cost:
            if is_infinite_flow and rein is not None:
                rein.hp = max(1, rein.hp - hp_cost)
                texts.append(f"⚖️ 选择带来了代价：{rein.name} 损失 {hp_cost} 点生命（{rein.hp}/{rein.max_hp}）")
            else:
                if session.state.investigators:
                    session.state.investigators[0].hp = max(1, session.state.investigators[0].hp - hp_cost)
                texts.append(f"⚖️ 选择带来了代价：损失 {hp_cost} 点生命")
        if san_cost:
            for inv in session.state.investigators:
                inv.san = max(0, inv.san - san_cost)
            texts.append(f"⚖️ 这个决定让人不安：全员 SAN -{san_cost}")
        if clue:
            texts.append(f"⚖️ 你们的决定改变了局面，解锁了新线索：{clue}")
        elif reward:
            texts.append(f"⚖️ {reward}")
        session.state.resolved_elements.add(mark)
        return texts

    if kind == "interaction":
        clue = opt.get("clue", "")
        hp_cost = opt.get("hp_cost", 0) or 0
        san_cost = opt.get("san_cost", 0) or 0
        result = opt.get("result_text", "") or ""
        if clue:
            session.state.resolved_elements.add(clue)
        if hp_cost:
            if is_infinite_flow and rein is not None:
                rein.hp = max(1, rein.hp - hp_cost)
                texts.append(f"💥 {rein.name} 损失 {hp_cost} 点生命（{rein.hp}/{rein.max_hp}）")
            else:
                if session.state.investigators:
                    session.state.investigators[0].hp = max(1, session.state.investigators[0].hp - hp_cost)
                texts.append(f"💥 损失 {hp_cost} 点生命")
        if san_cost:
            for inv in session.state.investigators:
                inv.san = max(0, inv.san - san_cost)
            texts.append(f"😱 理智受损：全员 SAN -{san_cost}")
        if result:
            texts.append(f"📜 {result}")
        elif clue:
            texts.append(f"📜 有了新发现：{clue}")
        session.state.resolved_elements.add(mark)
        return texts

    return texts


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


def _transition_hint(from_type: str, to_type: str) -> str:
    """根据场景类型切换方向生成叙事指引。"""
    hints = {
        ("investigation", "combat"): "调查线索引向了危险——请体现从思考到行动的急剧转折，紧张感猛然升级。",
        ("exploration", "combat"): "探索中的未知变成了迫在眉睫的威胁——请渲染'来不及反应'的突袭感。",
        ("social", "combat"): "对话破裂、谈判失败——请刻画从言语交锋到暴力冲突的那个临界瞬间。",
        ("story", "combat"): "剧情推进到了对抗点——请为这场不可避免的冲突做一个仪式感十足的开场。",
        ("combat", "rest"): "战斗后的喘息——请描述劫后余生的疲惫、伤口和沉默中未说出口的侥幸。",
        ("combat", "story"): "战斗结束，故事继续——请自然地让队伍从战斗状态回到叙事节奏。",
        ("horror", "combat"): "恐惧化为了实体——请描绘从心理压迫到物理对抗的尖叫般的转变。",
        ("rest", "combat"): "平静被无情打破——休整中的安逸被突如其来的威胁撕裂。",
    }
    return hints.get((from_type, to_type), "")


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
    mode: str = "ai"  # "ai" | "human" | "live"   live=展示选项+超时AI接手


@app.get("/api/stream")
async def stream(
    host: str = "http://localhost:11434",
    kp: str = "gemma4:12b",
    player: str = "ornith:9b",
    turns: int = Query(default=12, ge=1, le=200),
    seed: str = Query(default="", max_length=32),
    mode: Literal["ai", "human", "live"] = "ai",
    compose_modules: bool = True,
    kp_api_key: str = Query(default="", max_length=256),
    player_host: str = "http://localhost:11434",
    force_pickup: bool = False,
    vote_seconds: int = Query(default=60, ge=5, le=600),
    force_combat: bool = False,
    world: str = Query(default="", max_length=32),
    leader: str = Query(default="", max_length=32),
    load_profile: bool = False,
):
    try:
        seed_val = int(seed) if seed else None
    except ValueError:
        seed_val = None

    # 使用秒级时间戳 + UUID 后缀，避免同秒并发启动时 sid 冲突。
    sid = f"web_{datetime.now().strftime('%m%d_%H%M%S')}_{uuid4().hex[:8]}"

    async def _stream_with_cleanup():
        try:
            async for chunk in event_stream(
                host, kp, player, turns, seed_val, mode, compose_modules,
                kp_api_key=kp_api_key, player_host=player_host, force_pickup=force_pickup,
                vote_seconds=vote_seconds, force_combat=force_combat, world=world,
                sid=sid, leader=leader, load_profile=load_profile,
            ):
                yield chunk
        finally:
            _sessions.pop(sid, None)
            _vote_tallies.pop(sid, None)
            _vote_queues.pop(sid, None)

    return StreamingResponse(
        _stream_with_cleanup(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── WebSocket 端点（公网用：CF quick tunnel 缓冲 SSE，WS 实时转发不缓冲）──


def _parse_sse_chunk(chunk: str) -> tuple[str, dict]:
    """解析 _sse() 生成的 "event: xxx\ndata: {...}\n\n" 字符串为 (event, data)。"""
    event = "message"
    data_str = ""
    for line in chunk.split("\n"):
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            data_str = line[6:]
    try:
        data = json.loads(data_str) if data_str else {}
    except Exception:
        data = {"text": data_str}
    return event, data


@app.websocket("/api/ws")
async def ws_stream(
    websocket: WebSocket,
    host: str = "http://localhost:11434",
    kp: str = "gemma4:12b",
    player: str = "ornith:9b",
    turns: int = Query(default=12, ge=1, le=200),
    seed: str = Query(default="", max_length=32),
    mode: Literal["ai", "human", "live"] = "ai",
    compose_modules: bool = True,
    kp_api_key: str = Query(default="", max_length=256),
    player_host: str = "http://localhost:11434",
    force_pickup: bool = False,
    vote_seconds: int = Query(default=60, ge=5, le=600),
    force_combat: bool = False,
    world: str = Query(default="", max_length=32),
    leader: str = Query(default="", max_length=32),
    load_profile: bool = False,
):
    """WebSocket 版事件流：与 /api/stream 同一套 event_stream，事件以 JSON 逐条推送。

    解决 Cloudflare quick tunnel 缓冲 SSE 导致公网事件不实时的问题。
    """
    await websocket.accept()
    try:
        seed_val = int(seed) if seed else None
    except ValueError:
        seed_val = None

    sid = f"web_{datetime.now().strftime('%m%d_%H%M%S')}_{uuid4().hex[:8]}"

    try:
        async for chunk in event_stream(
            host, kp, player, turns, seed_val, mode, compose_modules,
            kp_api_key=kp_api_key, player_host=player_host, force_pickup=force_pickup,
            vote_seconds=vote_seconds, force_combat=force_combat, world=world,
            sid=sid, leader=leader, load_profile=load_profile,
        ):
            event, data = _parse_sse_chunk(chunk)
            await websocket.send_json({"event": event, "data": data})
    except WebSocketDisconnect:
        pass
    finally:
        _sessions.pop(sid, None)
        _vote_tallies.pop(sid, None)
        _vote_queues.pop(sid, None)


# ── 投票端点 ───────────────────────────────

class VoteRequest(BaseModel):
    choice: Literal["a", "b", "c"]
    counts: dict = {}  # {a: 0, b: 0, c: 0}，客户端自报数据，服务端会忽略，仅接受以兼容旧前端
    session_id: str = Field(default="", max_length=64)


@app.post("/api/vote")
async def handle_vote(req: VoteRequest):
    """接收前端投票请求（键盘 a/b/c 或点击）。
    只累加到该场次的投票计数里；真正的选择由投票窗口结束后的多数票决定
    （见 event_stream 里的 VOTE_WINDOW_SECONDS 等待逻辑），而不是第一票立即生效。
    忽略客户端自报的 counts，避免信任前端本地计数。
    """
    sid = req.session_id
    if not sid:
        # 兼容旧客户端：仅在当前恰好一个活跃会话时回退；
        # 多会话并发时必须显式传 session_id，防止跨会话串票。
        if len(_vote_tallies) == 1:
            sid = next(iter(_vote_tallies.keys()))
        else:
            return {"accepted": False, "reason": "missing session_id"}
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


# ── 无限流强化端点 ───────────────────────────


class TalentPurchaseRequest(BaseModel):
    talent_id: str = Field(..., max_length=64)
    session_id: str = Field(default="", max_length=64)


@app.get("/api/talents")
async def list_talents(session_id: str = Query(default="", max_length=64)):
    """查询强化树 + 当前轮回者状态（可用强化/已购强化/AP）。"""
    from trpg_agent.infinite_flow.talents import TalentCatalog
    catalog = TalentCatalog.load()
    session = _sessions.get(session_id) if session_id else None
    rein = None
    if session is not None and session.state is not None:
        rein = session.state.reincarnator

    talents = [
        {
            "id": t.id, "name": t.name, "line": t.line, "level": t.level,
            "description": t.description, "requires": t.requires,
            "effects": t.effects,
        }
        for t in catalog.talents.values()
    ]
    resp: dict = {
        "talents": talents,
        "cost_per_level": catalog.cost_per_level,
    }
    if rein is not None:
        resp["reincarnator"] = rein.to_dict()
        resp["available"] = [t.id for t in catalog.available_for(rein)]
        resp["stats_text"] = (
            f"力量 {rein.strength} | 敏捷 {rein.agility} | 精神 {rein.spirit} "
            f"| HP {rein.hp}/{rein.max_hp} | AP {rein.ap}"
        )
    return resp


@app.post("/api/talents/purchase")
async def purchase_talent(req: TalentPurchaseRequest):
    """购买强化——校验前置 + AP，应用效果到轮回者。"""
    from trpg_agent.infinite_flow.talents import TalentCatalog
    catalog = TalentCatalog.load()
    session = _sessions.get(req.session_id) if req.session_id else None
    if session is None or session.state is None or session.state.reincarnator is None:
        return {"ok": False, "reason": "session 不存在或不是无限流模式"}

    rein = session.state.reincarnator
    ok, msg = catalog.purchase(rein, req.talent_id)
    if not ok:
        return {"ok": False, "reason": msg}
    return {
        "ok": True,
        "message": msg,
        "reincarnator": rein.to_dict(),
        "available": [t.id for t in catalog.available_for(rein)],
        "stats_text": (
            f"力量 {rein.strength} | 敏捷 {rein.agility} | 精神 {rein.spirit} "
            f"| HP {rein.hp}/{rein.max_hp} | AP {rein.ap}"
        ),
    }


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
