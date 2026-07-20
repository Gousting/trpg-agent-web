"""多智能体 COC 跑团 — 接入检定/战斗/SAN 规则引擎。

用法:
    uv run python tests/test_multi_agent.py --turns 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.session import Session
from trpg_agent.llm.client import OllamaClient
from trpg_agent.memory.game_state import Investigator, Npc, Quest


# ═══════════════════════════════════════════════════════
# 调查员（完整技能+物品）
# ═══════════════════════════════════════════════════════

DEFAULT_INVESTIGATORS = [
    {
        "name": "陈明",
        "hp": 12, "max_hp": 12, "san": 60, "max_san": 60, "luck": 50,
        "skills": {"侦查": 60, "图书馆": 50, "说服": 40, "格斗": 50, "潜行": 45, "手枪": 45, "急救": 30},
        "inventory": ["手电筒", "警徽", ".38左轮手枪"],
        "personality": "退役刑警，沉默寡言但观察力极强。说话简短直接，先侦察再行动。",
    },
    {
        "name": "林晓",
        "hp": 10, "max_hp": 10, "san": 70, "max_san": 70, "luck": 45,
        "skills": {"医学": 65, "急救": 60, "心理学": 50, "神秘学": 30, "侦查": 35, "闪避": 40},
        "inventory": ["急救包", "笔记本", "相机"],
        "personality": "年轻法医，好奇心旺盛到近乎鲁莽。喜欢记笔记、拍照取证，紧张时会碎碎念。",
    },
    {
        "name": "王刚",
        "hp": 15, "max_hp": 15, "san": 40, "max_san": 40, "luck": 55,
        "skills": {"格斗": 70, "投掷": 50, "攀爬": 55, "恐吓": 45, "急救": 25},
        "inventory": ["棒球棍", "打火机", "香烟"],
        "personality": "码头工人，身强力壮。遇事先动手再说，对超自然事物本能排斥。",
    },
]

OPENING_SCENE = (
    "1928年深秋，阿卡姆市郊外废弃的松林疗养院。"
    "你们各自收到匿名信约在此地见面。大门虚掩，二楼透出微弱灯光，空气中有腐臭味。"
)


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

async def list_models(host: str) -> list[str]:
    async with httpx.AsyncClient(timeout=5) as cl:
        resp = await cl.get(f"{host}/api/tags")
        models = resp.json().get("models", [])
        models.sort(key=lambda m: m["size"], reverse=True)
        return [m["name"] for m in models]


async def chat_retry(client: OllamaClient, system: str, user_msg: str,
                     temperature: float = 0.8, max_tokens: int = 2000,
                     retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            response = await client.chat(
                system,
                [{"role": "user", "content": user_msg}],
                options={"temperature": temperature, "num_predict": max_tokens},
            )
            if response and response.strip():
                return response.strip()
            if attempt < retries:
                max_tokens = int(max_tokens * 1.5)
                await asyncio.sleep(1)
        except Exception:
            if attempt < retries:
                await asyncio.sleep(2)
    return ""


# ═══════════════════════════════════════════════════════
# 智能体 prompt（含数值状态）
# ═══════════════════════════════════════════════════════

PLAYER_PROMPT = """你是 {name}，克苏鲁的呼唤跑团调查员。

性格：{personality}
HP: {hp}/{max_hp}  SAN: {san}/{max_san}  LUCK: {luck}
技能：{skills}
物品：{items}
状态：{conditions}

用第一人称描述 {name} 的行动，1-2句话。可以根据技能尝试检定。"""


KP_PROMPT = """你是克苏鲁的呼唤主持人。用中文叙述。

{scene}

规则：
- 不替调查员说话
- 检定结果已给出，按结果叙述
- 失败的检定要有后果
- 保持恐怖氛围"""


# ═══════════════════════════════════════════════════════
# 玩家智能体
# ═══════════════════════════════════════════════════════

async def player_act(client: OllamaClient, inv_data: dict,
                     inv_state: Investigator, kp_narration: str) -> str:
    system = PLAYER_PROMPT.format(
        name=inv_data["name"], personality=inv_data["personality"],
        hp=inv_state.hp, max_hp=inv_state.max_hp,
        san=inv_state.san, max_san=inv_state.max_san,
        luck=inv_state.luck,
        skills=json.dumps(inv_state.skills, ensure_ascii=False),
        items=", ".join(inv_state.inventory) if inv_state.inventory else "无",
        conditions=", ".join(inv_state.conditions) if inv_state.conditions else "无",
    )
    user = f"主持人叙述：{kp_narration[:600]}\n\n{inv_data['name']}的行动："
    result = await chat_retry(client, system, user, temperature=0.9, max_tokens=2000)
    return result if result else f"（{inv_data['name']} 谨慎地观察四周）"


# ═══════════════════════════════════════════════════════
# KP 智能体
# ═══════════════════════════════════════════════════════

async def kp_narrate(client: OllamaClient, action: str, speaker: str,
                     scene_text: str, dice_context: str = "") -> str:
    system = KP_PROMPT.format(scene=scene_text)

    if dice_context:
        user = f"[检定] {dice_context}\n\n[{speaker}] {action}\n\n请叙述结果："
    else:
        user = f"[{speaker}] {action}\n\n请叙述："

    result = await chat_retry(client, system, user, temperature=0.8, max_tokens=2500)
    return result if result else "（KP 沉思……）"


# ═══════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════

async def run_game(host: str, kp_model: str, player_models: list[str],
                   turns: int) -> None:
    # ── 初始化 ──────────────────────────────────
    print("🔌 连接 Ollama ...")
    available = await list_models(host)
    for m in [kp_model] + player_models:
        if m not in available:
            print(f"❌ '{m}' 不可用")
            return

    kp_client = OllamaClient(host, kp_model, num_ctx=8192, timeout=180)
    player_clients = {
        inv["name"]: OllamaClient(host, inv["model"], num_ctx=4096, timeout=120)
        for inv in DEFAULT_INVESTIGATORS
    }

    sid = f"demo_{datetime.now().strftime('%m%d_%H%M%S')}"
    old_dir = Path("data/sessions") / sid
    if old_dir.exists():
        shutil.rmtree(old_dir)
    session = Session(sid, auto_save_interval=0)

    for inv_data in DEFAULT_INVESTIGATORS:
        inv = Investigator(
            name=inv_data["name"], hp=inv_data["hp"],
            max_hp=inv_data["max_hp"], san=inv_data["san"],
            max_san=inv_data["max_san"], luck=inv_data["luck"],
            skills=inv_data["skills"],
            inventory=list(inv_data.get("inventory", [])),
        )
        session.state.investigators.append(inv)
    session.state.location = "废弃的松林疗养院门前"

    # ── 标题 ──────────────────────────────────
    print()
    print("=" * 56)
    print("🎭 COC 跑团（接入检定引擎）")
    print(f"   KP: {kp_model}")
    for inv in DEFAULT_INVESTIGATORS:
        print(f"   🎲 {inv['name']} ({inv['model']})  "
              f"HP:{inv['hp']}/{inv['max_hp']} SAN:{inv['san']}/{inv['max_san']}")
    print("=" * 56)

    # ── KP 开场 ─────────────────────────────────
    scene_line = (f"场景：{OPENING_SCENE}\n"
                  f"调查员：{', '.join(i['name'] for i in DEFAULT_INVESTIGATORS)}")
    t0 = time.time()
    opening = await kp_narrate(kp_client, "游戏开始，描述场景。", "", scene_line)
    dt = time.time() - t0
    session.record_turn("(游戏开始)", opening)
    print(f"\n📖 KP ({dt:.0f}s):\n{opening}\n")

    # ── 游戏循环 ────────────────────────────────
    player_order = [inv["name"] for inv in DEFAULT_INVESTIGATORS]
    last_narration = opening

    for turn in range(turns):
        speaker = player_order[turn % len(player_order)]
        inv_data = next(inv for inv in DEFAULT_INVESTIGATORS if inv["name"] == speaker)
        inv_state = session.state.find_investigator(speaker)
        player_client = player_clients[speaker]

        # 1. 玩家行动
        t0 = time.time()
        action = await player_act(player_client, inv_data, inv_state, last_narration)
        dt_p = time.time() - t0

        # 2. 检定路由 — 判断是否需要掷骰
        dice_context = ""
        try:
            dice_context, roll_req = await session.classify_and_resolve(
                kp_client, action
            )
        except Exception:
            pass

        # 3. KP 叙述
        t0 = time.time()
        scene = session.state.scene_summary()
        narration = await kp_narrate(kp_client, action, speaker, scene, dice_context)
        dt_k = time.time() - t0

        # 4. 记录回合
        session.record_turn(action, narration, speaker=speaker)
        last_narration = narration

        # 5. 输出
        status = f"HP:{inv_state.hp}/{inv_state.max_hp} SAN:{inv_state.san}/{inv_state.max_san}"
        print(f"🎲 {speaker} [{status}] ({dt_p:.0f}s):\n   {action}")
        if dice_context:
            print(f"   🎯 {dice_context}")
        print(f"\n📖 KP ({dt_k:.0f}s):\n{narration}\n")
        print("-" * 48 + "\n")

    # ── 结算 ──────────────────────────────────
    print("=" * 56)
    print(session.loaded_state_summary())
    print("=" * 56)


def main():
    parser = argparse.ArgumentParser(description="多智能体 COC 跑团（检定引擎）")
    parser.add_argument("--host", default="http://192.168.0.108:11434")
    parser.add_argument("--kp", default="gemma4:12b")
    parser.add_argument("--players", default="ornith:9b,ornith:9b,ornith:9b")
    parser.add_argument("--turns", type=int, default=6)
    args = parser.parse_args()

    player_models = [m.strip() for m in args.players.split(",")]
    for i, inv in enumerate(DEFAULT_INVESTIGATORS):
        inv["model"] = player_models[i % len(player_models)]

    asyncio.run(run_game(args.host, args.kp, player_models, args.turns))


if __name__ == "__main__":
    main()
