"""TRPG 多智能体 Web 界面 — FastAPI + SSE 实时推送游戏事件。

启动:
    uv run python -m trpg_agent.web_server
    # 浏览器打开 http://localhost:8766
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.memory.game_state import Investigator

log = logging.getLogger(__name__)

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

OPENING = "1928年深秋，阿卡姆市郊外废弃的松林疗养院。你们各自收到匿名信约在此地见面。大门虚掩，二楼透出微弱灯光。"

PLAYER_PROMPT = """你是 {name}，克苏鲁的呼唤调查员。
性格：{personality}
HP:{hp}/{max_hp} SAN:{san}/{max_san}
技能：{skills}  物品：{items}
用第一人称描述行动，1-2句话。"""

KP_PROMPT = """你是克苏鲁的呼唤主持人。
{scene}
规则：不替调查员说话；检定结果已给出，按结果叙述；保持恐怖氛围。"""


# ═══════════════════════════════════════════════════════
# Web 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(title="TRPG 多智能体跑团")

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
    """当前所有调查员的状态快照。"""
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


async def event_stream(host: str, kp_model: str, player_model: str, turns: int):
    """SSE 事件流 — 每轮生成事件推送给前端。"""
    yield f"data: {json.dumps({'type': 'status', 'text': '连接 Ollama...'}, ensure_ascii=False)}\n\n"

    # 检测模型
    async with httpx.AsyncClient(timeout=5) as cl:
        resp = await cl.get(f"{host}/api/tags")
        available = [m["name"] for m in resp.json().get("models", [])]

    kp_client = OllamaClient(host, kp_model, num_ctx=8192, timeout=180)
    player_clients = {}
    for inv in INVESTIGATORS:
        player_clients[inv["name"]] = OllamaClient(host, player_model, num_ctx=4096, timeout=120)

    # Session
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
    session.state.location = "废弃的松林疗养院门前"

    # 发送初始状态
    yield f"data: {json.dumps({'type': 'init', 'investigators': INVESTIGATORS, 'kp_model': kp_model, 'player_model': player_model, 'opening': OPENING}, ensure_ascii=False)}\n\n"

    # KP 开场
    scene_line = f"场景：{OPENING}\n调查员：{', '.join(i['name'] for i in INVESTIGATORS)}"
    system = KP_PROMPT.format(scene=scene_line)
    opening = await chat_retry(kp_client, system, "游戏开始，描述场景。", temperature=0.8, max_tokens=2500)
    session.record_turn("(游戏开始)", opening or "（KP 沉思……）")
    yield f"data: {json.dumps({'type': 'kp_narration', 'speaker': 'KP', 'text': opening, 'state': state_snapshot(session)}, ensure_ascii=False)}\n\n"
    await asyncio.sleep(1)

    # 游戏循环
    last_narration = opening or ""
    player_order = [inv["name"] for inv in INVESTIGATORS]

    for turn in range(turns):
        speaker = player_order[turn % len(player_order)]
        inv_data = next(inv for inv in INVESTIGATORS if inv["name"] == speaker)
        inv_state = session.state.find_investigator(speaker)

        # 玩家行动
        player_system = PLAYER_PROMPT.format(
            name=inv_data["name"], personality=inv_data["personality"],
            hp=inv_state.hp, max_hp=inv_state.max_hp,
            san=inv_state.san, max_san=inv_state.max_san,
            skills=json.dumps(inv_state.skills, ensure_ascii=False),
            items=", ".join(inv_state.inventory) if inv_state.inventory else "无",
        )
        action = await chat_retry(
            player_clients[speaker], player_system,
            f"主持人叙述：{last_narration[:600]}\n\n{inv_data['name']}的行动：",
            temperature=0.9, max_tokens=2000,
        )
        if not action:
            action = f"（{speaker} 谨慎地观察四周）"

        yield f"data: {json.dumps({'type': 'player_action', 'speaker': speaker, 'text': action, 'color': inv_data['color']}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.3)

        # 检定
        dice_context = ""
        try:
            dice_context, _ = await session.classify_and_resolve(kp_client, action)
        except Exception:
            pass

        if dice_context:
            yield f"data: {json.dumps({'type': 'dice_roll', 'speaker': speaker, 'text': dice_context}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

        # KP 叙述
        scene = session.state.scene_summary()
        kp_system = KP_PROMPT.format(scene=scene)
        if dice_context:
            kp_user = f"[检定] {dice_context}\n\n[{speaker}] {action}\n\n请叙述结果："
        else:
            kp_user = f"[{speaker}] {action}\n\n请叙述："

        narration = await chat_retry(kp_client, kp_system, kp_user, temperature=0.8, max_tokens=2500)
        if not narration:
            narration = "（KP 沉思片刻……）"

        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration

        yield f"data: {json.dumps({'type': 'kp_narration', 'speaker': 'KP', 'text': narration, 'state': state_snapshot(session)}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.5)

    yield f"data: {json.dumps({'type': 'done', 'summary': session.loaded_state_summary()}, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(
    host: str = "http://192.168.0.108:11434",
    kp: str = "gemma4:12b",
    player: str = "ornith:9b",
    turns: int = 12,
):
    return StreamingResponse(
        event_stream(host, kp, player, turns),
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
