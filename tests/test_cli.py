"""CLI 入口测试。"""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

import trpg_agent
import trpg_agent.__main__ as cli


class DummySession:
    def __init__(self, session_id: str, *, max_context: int):
        self.session_id = session_id
        self.max_context = max_context
        self.state = SimpleNamespace(scene_id="foyer")
        self.persist_calls = 0

    def load_adventure(self, adventure_id: str):
        return SimpleNamespace(
            id=adventure_id,
            title="古屋疑云",
            get_scene=lambda _scene_id: SimpleNamespace(title="古屋门厅", description="desc"),
        )

    def persist(self) -> None:
        self.persist_calls += 1

    def summary(self) -> str:
        return "dummy summary"


class DummyClient:
    created: list[tuple[str, str, int]] = []
    closed = 0

    def __init__(self, *, host: str, model: str, num_ctx: int):
        self.host = host
        self.model = model
        self.num_ctx = num_ctx
        DummyClient.created.append((host, model, num_ctx))

    async def aclose(self) -> None:
        DummyClient.closed += 1


def _args(**overrides) -> argparse.Namespace:
    data = {
        "host": "http://localhost:11434",
        "model": "qwen2.5:7b",
        "session_id": "cli_test",
        "adventure": "鬼屋",
        "num_ctx": 4096,
        "log_level": "INFO",
        "check": False,
        "once": None,
        "skip_preflight": False,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def test_package_exports_are_lazy():
    assert "Room" in trpg_agent.__all__
    assert trpg_agent.Room.__name__ == "Room"


def test_run_cli_check_mode_skips_preflight(monkeypatch, capsys):
    calls = {"preflight": 0}

    monkeypatch.setattr(cli, "Session", DummySession)
    monkeypatch.setattr(cli, "_print_banner", lambda session, adventure: print("banner"))
    monkeypatch.setattr(cli, "check_ollama", lambda host, model: calls.__setitem__("preflight", calls["preflight"] + 1))
    monkeypatch.setattr(cli, "DEFAULT_PREFLIGHT", True)

    rc = asyncio.run(cli._run_cli(_args(check=True)))

    assert rc == 0
    assert calls["preflight"] == 0
    assert "自检通过" in capsys.readouterr().out


def test_run_cli_once_mode_runs_preflight(monkeypatch, capsys):
    calls = {"preflight": 0}
    DummyClient.created.clear()
    DummyClient.closed = 0

    async def fake_run_turn(session, client, player_input, *, adventure=None):
        assert player_input == "检查门厅"
        return "门厅里弥漫着霉味。", "侦查 成功"

    monkeypatch.setattr(cli, "Session", DummySession)
    monkeypatch.setattr(cli, "OllamaClient", DummyClient)
    monkeypatch.setattr(cli, "_run_turn", fake_run_turn)
    monkeypatch.setattr(cli, "check_ollama", lambda host, model: calls.__setitem__("preflight", calls["preflight"] + 1) or True)
    monkeypatch.setattr(cli, "DEFAULT_PREFLIGHT", True)

    rc = asyncio.run(cli._run_cli(_args(once="检查门厅")))

    assert rc == 0
    assert calls["preflight"] == 1
    assert DummyClient.created == [("http://localhost:11434", "qwen2.5:7b", 4096)]
    assert DummyClient.closed == 1
    out = capsys.readouterr().out
    assert "检定: 侦查 成功" in out
    assert "KP: 门厅里弥漫着霉味。" in out


def test_run_cli_interactive_quit_uses_async_reader(monkeypatch, capsys):
    DummyClient.created.clear()
    DummyClient.closed = 0

    async def fake_read(_prompt: str) -> str:
        return "/quit"

    async def should_not_run(*args, **kwargs):
        raise AssertionError("/quit should exit before a turn runs")

    monkeypatch.setattr(cli, "Session", DummySession)
    monkeypatch.setattr(cli, "OllamaClient", DummyClient)
    monkeypatch.setattr(cli, "_read_user_input", fake_read)
    monkeypatch.setattr(cli, "_run_turn", should_not_run)
    monkeypatch.setattr(cli, "DEFAULT_PREFLIGHT", False)

    rc = asyncio.run(cli._run_cli(_args()))

    assert rc == 0
    assert DummyClient.closed == 1
    assert "输入调查员行动开始跑团" in capsys.readouterr().out