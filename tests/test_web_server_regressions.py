"""web_server 关键回归测试。

覆盖本次修复点：
1. 继承轮回者时开局 HP 回满
2. reward_ap=0 必须按模块声明生效，不得回退默认 AP
3. 投票接口在多会话且缺失 session_id 时拒绝请求
4. live 队友提示使用会话实时状态
5. 前端世界观标签支持 infinite_flow
6. 前端 showVoteBar() 不再引用 startGame() 局部 selectedMode（避免 ReferenceError），
   改用模块级 currentMode/currentWorld + WORLD_UI_PRESETS 驱动文案与显隐
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from trpg_agent.combat.encounter import CombatEncounter
from trpg_agent.memory.game_state import Investigator, Reincarnator
from trpg_agent_web import web_server


def _parse_sse_chunk(chunk: str) -> tuple[str, dict]:
    event = ""
    data = {}
    for line in chunk.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):])
    return event, data


class TestWebServerRegressions:
    def test_resolve_outcome_ap_respects_zero(self):
        enc = CombatEncounter.from_dict(
            {
                "id": "boss",
                "outcomes": {
                    "victory": {"reward_ap": 4},
                    "defeat": {"reward_ap": 1},
                    "flee": {"reward_ap": 0},
                },
            }
        )
        assert web_server._resolve_outcome_ap("victory", enc) == 4
        assert web_server._resolve_outcome_ap("defeat", enc) == 1
        assert web_server._resolve_outcome_ap("flee", enc) == 0

    def test_resolve_outcome_ap_fallback_when_outcome_missing(self):
        assert web_server._resolve_outcome_ap("victory", None) == 3
        assert web_server._resolve_outcome_ap("flee", None) == 1

    def test_build_teammate_prompt_uses_runtime_state(self):
        inv_a = Investigator(
            name="林晓", hp=4, max_hp=10, san=33, max_san=70, luck=40,
            skills={}, inventory=[]
        )
        inv_b = Investigator(
            name="王刚", hp=2, max_hp=15, san=18, max_san=40, luck=45,
            skills={}, inventory=[]
        )

        state = SimpleNamespace(
            investigators=[inv_a, inv_b],
            find_investigator=lambda name: inv_a if name == "林晓" else (inv_b if name == "王刚" else None),
        )
        session = SimpleNamespace(state=state)

        teammates = [
            {"name": "林晓", "hp": 10, "max_hp": 10, "san": 70, "max_san": 70},
            {"name": "王刚", "hp": 15, "max_hp": 15, "san": 40, "max_san": 40},
        ]
        prompt_data = web_server._build_teammate_prompt_data(session, teammates)

        assert prompt_data[0]["hp"] == 4
        assert prompt_data[0]["san"] == 33
        assert prompt_data[1]["hp"] == 2
        assert prompt_data[1]["san"] == 18

    def test_vote_missing_session_id_rejected_when_multi_active(self):
        web_server._vote_tallies.clear()
        web_server._vote_queues.clear()
        web_server._vote_tallies["s1"] = {}
        web_server._vote_tallies["s2"] = {}

        req = web_server.VoteRequest(choice="a", session_id="")
        out = asyncio.run(web_server.handle_vote(req))

        assert out["accepted"] is False
        assert out["reason"] == "missing session_id"

    def test_load_profile_heals_reincarnator_to_full_hp(self, monkeypatch):
        rein = Reincarnator(name="轮回者", max_hp=12, hp=3, strength=10, agility=10, spirit=10, ap=5)

        async def _fake_chat_generate(*args, **kwargs):
            return "开场叙述"

        async def _fake_speak(*args, **kwargs):
            return None

        monkeypatch.setattr(web_server, "_load_reincarnator", lambda name="轮回者": rein)
        monkeypatch.setattr(web_server, "_chat_generate", _fake_chat_generate)
        monkeypatch.setattr(web_server, "_speak", _fake_speak)
        monkeypatch.setattr(
            web_server,
            "_scene_for_room",
            lambda room_type: {"image": "", "location": "", "mood": "exploration", "score": 1.0},
        )
        monkeypatch.setattr(web_server, "_make_client", lambda *args, **kwargs: object())
        monkeypatch.setattr(web_server, "_is_remote_host", lambda host: True)

        async def _run() -> dict:
            gen = web_server.event_stream(
                host="http://localhost:11434",
                kp_model="x",
                player_model="y",
                turns=0,
                seed=1,
                mode="ai",
                compose_modules=False,
                kp_api_key="",
                player_host="http://localhost:11434",
                force_pickup=False,
                vote_seconds=10,
                force_combat=False,
                world="infinite_flow",
                sid="regression_hp_full",
                leader="",
                load_profile=True,
            )
            try:
                async for chunk in gen:
                    evt, data = _parse_sse_chunk(chunk)
                    if evt == "init":
                        return data
            finally:
                await gen.aclose()
            raise AssertionError("未收到 init 事件")

        init_payload = asyncio.run(_run())
        assert init_payload["reincarnator"]["hp"] == init_payload["reincarnator"]["max_hp"] == 12

    def test_infinite_flow_init_includes_two_ai_teammates(self, monkeypatch):
        # 回归：无限流模式曾经始终发送 investigators=[]，导致前端只显示轮回者卡片，
        # 缺失设计中"轮回者主控 + 2 AI 队友"的队伍展示。
        rein = Reincarnator(name="轮回者", max_hp=12, hp=12, strength=10, agility=10, spirit=10, ap=0)

        async def _fake_chat_generate(*args, **kwargs):
            return "开场叙述"

        async def _fake_speak(*args, **kwargs):
            return None

        monkeypatch.setattr(web_server, "_load_reincarnator", lambda name="轮回者": rein)
        monkeypatch.setattr(web_server, "_chat_generate", _fake_chat_generate)
        monkeypatch.setattr(web_server, "_speak", _fake_speak)
        monkeypatch.setattr(
            web_server,
            "_scene_for_room",
            lambda room_type: {"image": "", "location": "", "mood": "exploration", "score": 1.0},
        )
        monkeypatch.setattr(web_server, "_make_client", lambda *args, **kwargs: object())
        monkeypatch.setattr(web_server, "_is_remote_host", lambda host: True)

        async def _run() -> dict:
            gen = web_server.event_stream(
                host="http://localhost:11434",
                kp_model="x",
                player_model="y",
                turns=0,
                seed=1,
                mode="ai",
                compose_modules=False,
                kp_api_key="",
                player_host="http://localhost:11434",
                force_pickup=False,
                vote_seconds=10,
                force_combat=False,
                world="infinite_flow",
                sid="regression_teammates",
                leader="",
                load_profile=True,
            )
            try:
                async for chunk in gen:
                    evt, data = _parse_sse_chunk(chunk)
                    if evt == "init":
                        return data
            finally:
                await gen.aclose()
            raise AssertionError("未收到 init 事件")

        init_payload = asyncio.run(_run())
        names = [inv["name"] for inv in init_payload["investigators"]]
        assert names == ["林晓", "王刚"]

    def test_infinite_flow_speaker_is_reincarnator_not_coc_investigator(self, monkeypatch):
        # 回归：player_order 曾经始终取自 COC 的 INVESTIGATORS，导致无限流模式下
        # 行动日志里的发言人是"陈明"而不是轮回者本人。
        rein = Reincarnator(name="轮回者", max_hp=12, hp=12, strength=10, agility=10, spirit=10, ap=0)

        async def _fake_chat_generate(*args, **kwargs):
            return "开场叙述"

        async def _fake_speak(*args, **kwargs):
            return None

        monkeypatch.setattr(web_server, "_load_reincarnator", lambda name="轮回者": rein)
        monkeypatch.setattr(web_server, "_chat_generate", _fake_chat_generate)
        monkeypatch.setattr(web_server, "_speak", _fake_speak)
        monkeypatch.setattr(
            web_server,
            "_scene_for_room",
            lambda room_type: {"image": "", "location": "", "mood": "exploration", "score": 1.0},
        )
        monkeypatch.setattr(web_server, "_make_client", lambda *args, **kwargs: object())
        monkeypatch.setattr(web_server, "_is_remote_host", lambda host: True)

        async def _run() -> str:
            gen = web_server.event_stream(
                host="http://localhost:11434",
                kp_model="x",
                player_model="y",
                turns=1,
                seed=1,
                mode="ai",
                compose_modules=False,
                kp_api_key="",
                player_host="http://localhost:11434",
                force_pickup=True,
                vote_seconds=10,
                force_combat=False,
                world="infinite_flow",
                sid="regression_speaker",
                leader="",
                load_profile=True,
            )
            try:
                async for chunk in gen:
                    evt, data = _parse_sse_chunk(chunk)
                    if evt == "player_stream_start":
                        return data["speaker"]
            finally:
                await gen.aclose()
            raise AssertionError("未收到 player_stream_start 事件")

        speaker = asyncio.run(_run())
        assert speaker == "轮回者"

    def test_roster_for_world_returns_dedicated_roster_per_world(self):
        # 回归：_roster_for_world() 必须为已登记的世界观返回独立名单，
        # 未登记的世界观（含空字符串/coc）回退 COC 默认名单。
        assert [inv["name"] for inv in web_server._roster_for_world("harry_potter")] == [
            "凯尔", "艾米", "托马斯",
        ]
        assert [inv["name"] for inv in web_server._roster_for_world("coc")] == [
            "陈明", "林晓", "王刚",
        ]
        assert [inv["name"] for inv in web_server._roster_for_world("")] == [
            "陈明", "林晓", "王刚",
        ]
        assert [inv["name"] for inv in web_server._roster_for_world("unknown_world")] == [
            "陈明", "林晓", "王刚",
        ]

    def test_harry_potter_init_uses_dedicated_roster_not_coc_investigators(self, monkeypatch):
        # 回归：哈利波特世界观曾经没有独立角色名单，队伍展示/开场提示词都会
        # 沿用 COC 的"陈明/林晓/王刚"，与哈利波特设定不符。
        async def _fake_chat_generate(*args, **kwargs):
            return "开场叙述"

        async def _fake_speak(*args, **kwargs):
            return None

        monkeypatch.setattr(web_server, "_chat_generate", _fake_chat_generate)
        monkeypatch.setattr(web_server, "_speak", _fake_speak)
        monkeypatch.setattr(
            web_server,
            "_scene_for_room",
            lambda room_type: {"image": "", "location": "", "mood": "exploration", "score": 1.0},
        )
        monkeypatch.setattr(web_server, "_make_client", lambda *args, **kwargs: object())
        monkeypatch.setattr(web_server, "_is_remote_host", lambda host: True)

        async def _run() -> dict:
            gen = web_server.event_stream(
                host="http://localhost:11434",
                kp_model="x",
                player_model="y",
                turns=0,
                seed=1,
                mode="ai",
                compose_modules=False,
                kp_api_key="",
                player_host="http://localhost:11434",
                force_pickup=False,
                vote_seconds=10,
                force_combat=False,
                world="harry_potter",
                sid="regression_harry_potter_roster",
                leader="",
                load_profile=False,
            )
            try:
                async for chunk in gen:
                    evt, data = _parse_sse_chunk(chunk)
                    if evt == "init":
                        return data
            finally:
                await gen.aclose()
            raise AssertionError("未收到 init 事件")

        init_payload = asyncio.run(_run())
        names = [inv["name"] for inv in init_payload["investigators"]]
        assert names == ["凯尔", "艾米", "托马斯"]
        assert "陈明" not in names and "王刚" not in names

    def test_state_snapshot_merges_reincarnator_and_teammates(self):
        # 回归：_state_snapshot() 曾经在存在轮回者时直接 return，导致 AI 队友的
        # HP/SAN 更新永远无法同步到前端。
        rein = Reincarnator(name="轮回者", max_hp=12, hp=8, strength=10, agility=10, spirit=10, ap=2)
        inv = Investigator(name="林晓", hp=6, max_hp=10, san=50, max_san=70, luck=40, skills={}, inventory=[])
        state = SimpleNamespace(reincarnator=rein, investigators=[inv])
        session = SimpleNamespace(state=state)

        snap = web_server._state_snapshot(session)

        assert snap["reincarnator"]["hp"] == 8
        assert snap["林晓"]["hp"] == 6
        assert snap["林晓"]["san"] == 50

    def test_frontend_world_label_includes_infinite_flow_mapping(self):
        # 世界观文案已重构为 WORLD_UI_PRESETS 配置表（docs/world-driven-ui-plan.md），
        # 校验无限流条目及其舞台标签存在，而不再依赖已废弃的内联三元表达式写法。
        html = Path("trpg_agent_web/static/index.html").read_text(encoding="utf-8")
        assert "infinite_flow: {" in html
        assert "stageWorldLabel: '无限流'" in html
    def test_frontend_show_vote_bar_no_longer_references_stale_selected_mode(self):
        # 回归：showVoteBar() 曾引用 startGame() 局部的 selectedMode，跨函数访问会抛 ReferenceError。
        # 修复后应改用模块级 currentMode 变量，且代码中不应再出现 selectedMode 标识符。
        html = Path("trpg_agent_web/static/index.html").read_text(encoding="utf-8")
        assert "selectedMode" not in html
        assert "let currentMode = 'ai';" in html
        assert "currentMode === 'live'" in html

    def test_frontend_world_preset_controls_load_profile_visibility(self):
        # 回归：“继承轮回者”选项仅对无限流世界观有意义，应在非无限流下隐藏。
        html = Path("trpg_agent_web/static/index.html").read_text(encoding="utf-8")
        assert 'id="load-profile-label"' in html
        assert "showLoadProfile: true" in html

    def test_frontend_world_preset_toggles_char_card_preview_before_init(self):
        # 回归：切换世界观下拉框后，开局前的角色卡片预览曾一直显示全部 6 张卡片
        # （COC + 哈利波特），只有真正开始跑团、init 事件到达后才会被纠正。
        # applyWorldPreset() 现在也应根据 rosterCards 立即显隐角色卡片，
        # 使切换世界观在开局前就有“完整变化”。
        html = Path("trpg_agent_web/static/index.html").read_text(encoding="utf-8")
        assert "rosterCards: ['陈明', '林晓', '王刚']" in html
        assert "rosterCards: ['凯尔', '艾米', '托马斯']" in html
        assert "rosterCards: ['林晓', '王刚']" in html
        assert "const cardNames = preset.rosterCards || [];" in html
        assert "el.id !== 'card-reincarnator'" in html or "if (el.id === 'card-reincarnator') return;" in html

    def test_frontend_world_preset_updates_document_title(self):
        # 回归：浏览器标签页标题一直硬编码为 "COC TRPG 跑团"，切换世界观下拉框
        # 后标题不会跟着变化。applyWorldPreset() 现在应同步更新 document.title。
        html = Path("trpg_agent_web/static/index.html").read_text(encoding="utf-8")
        assert "document.title = preset.stageWorldLabel + ' TRPG 跑团';" in html