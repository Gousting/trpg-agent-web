"""T3：AI 队友系统——解析与兜底测试。

验证点：
1. _parse_teammates_action 正确解析"名字：行动"行
2. 容错：多余行/空内容/全半角冒号/无关文本
3. 失败兜底：LLM 异常返回空 dict（调用方走"谨慎观察"）
"""
import asyncio

import pytest

from trpg_agent_web.web_server import _ai_teammates_action, _parse_teammates_action

NAMES = ["林晓", "王刚"]


class TestParseTeammatesAction:
    def test_parses_full_width_colon(self):
        raw = "林晓：我蹲下来查看血迹。\n王刚：我挡在门口警戒。"
        assert _parse_teammates_action(raw, NAMES) == {
            "林晓": "我蹲下来查看血迹。",
            "王刚": "我挡在门口警戒。",
        }

    def test_parses_half_width_colon(self):
        raw = "林晓: 我翻看笔记本。\n王刚: 我检查门锁。"
        assert _parse_teammates_action(raw, NAMES) == {
            "林晓": "我翻看笔记本。",
            "王刚": "我检查门锁。",
        }

    def test_ignores_unrelated_lines(self):
        """多余叙述行（无名字前缀）被忽略，不影响结果。"""
        raw = "两个人警惕地互相看了一眼。\n林晓：我捡起地上的手电筒。\n王刚：我守住门口。"
        assert _parse_teammates_action(raw, NAMES) == {
            "林晓": "我捡起地上的手电筒。",
            "王刚": "我守住门口。",
        }

    def test_skips_empty_action(self):
        """名字前缀但内容为空的行跳过（调用方兜底）。"""
        raw = "林晓：\n王刚：我检查走廊。"
        assert _parse_teammates_action(raw, NAMES) == {"王刚": "我检查走廊。"}

    def test_partial_teammates(self):
        """只有部分队友出现时，只返回出现的。"""
        raw = "林晓：我记录下照片内容。"
        assert _parse_teammates_action(raw, NAMES) == {"林晓": "我记录下照片内容。"}

    def test_empty_input(self):
        assert _parse_teammates_action("", NAMES) == {}
        assert _parse_teammates_action("", NAMES) == {}


class TestAiTeammatesAction:
    def test_empty_teammates_returns_empty(self):
        assert asyncio.run(_ai_teammates_action("http://x", "m", [], {}, "叙述", "行动")) == {}

    def test_failure_falls_back_to_empty(self, monkeypatch):
        """LLM 调用异常 → 返回空 dict（不阻塞主循环）。"""
        from trpg_agent.llm.client import OllamaClient

        class _Boom:
            async def chat(self, *a, **k):
                raise RuntimeError("模型不可用")

        monkeypatch.setattr(OllamaClient, "chat", _Boom.chat)
        teammates = [{"name": "林晓", "hp": 10, "max_hp": 10, "san": 70, "max_san": 70}]
        result = asyncio.run(
            _ai_teammates_action("http://x", "m", teammates, {"name": "走廊"}, "叙述", "行动")
        )
        assert result == {}
