"""CLI 入口测试。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import trpg_agent
import trpg_agent.__main__ as cli
from trpg_agent.session import Session


class DummySession:
    def __init__(self, session_id: str, *, max_context: int):
        self.session_id = session_id
        self.max_context = max_context
        self.state = SimpleNamespace(scene_id="foyer")
        self.state.adventure_id = ""
        self.persist_calls = 0
        self.runtime_loaded = None
        self.compiled_loaded = None
        self.resumed = False

    def load_adventure(self, adventure_id: str):
        return SimpleNamespace(
            id=adventure_id,
            title="古屋疑云",
            get_scene=lambda _scene_id: SimpleNamespace(title="古屋门厅", description="desc"),
        )

    def load_runtime_adventure(self, adventure, *, adventure_id: str | None = None):
        self.runtime_loaded = (adventure, adventure_id)
        return adventure

    def load_compiled_adventure(self, bundle):
        self.compiled_loaded = bundle
        return bundle.adventure

    def resume_adventure(self):
        self.resumed = True
        return SimpleNamespace(
            id="saved_adv",
            title="已恢复模组",
            get_scene=lambda _scene_id: SimpleNamespace(title="恢复场景", description="desc"),
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
        "compose_modules": False,
        "module_start": None,
        "module_max_depth": 3,
        "module_seed": None,
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


def test_run_cli_can_load_compiled_modules(monkeypatch, capsys):
    DummyClient.created.clear()
    DummyClient.closed = 0

    class DummyComposer:
        def __init__(self, modules_dir):
            self.modules_dir = modules_dir

        def compile(self, *, seed=None, max_depth=3, start_module=None):
            assert seed == 7
            assert max_depth == 4
            assert start_module == "library_research"
            adventure = SimpleNamespace(
                id="composed_library",
                title="模块化冒险",
                get_scene=lambda _scene_id: SimpleNamespace(title="图书室", description="desc"),
            )
            return SimpleNamespace(adventure=adventure, source_id="composed_library")

    async def fake_read(_prompt: str) -> str:
        return "/quit"

    monkeypatch.setattr(cli, "Session", DummySession)
    monkeypatch.setattr(cli, "ModuleComposer", DummyComposer)
    monkeypatch.setattr(cli, "OllamaClient", DummyClient)
    monkeypatch.setattr(cli, "_read_user_input", fake_read)
    monkeypatch.setattr(cli, "DEFAULT_PREFLIGHT", False)

    rc = asyncio.run(cli._run_cli(_args(
        adventure=None,
        compose_modules=True,
        module_start="library_research",
        module_max_depth=4,
        module_seed=7,
    )))

    assert rc == 0
    assert DummyClient.closed == 1
    assert DummySession("x", max_context=1).compiled_loaded is None
    out = capsys.readouterr().out
    assert "模组: 模块化冒险 (composed_library)" in out


def test_load_requested_adventure_smoke_uses_real_module_composition():
    with TemporaryDirectory() as td:
        session = Session(
            "smoke_compose",
            max_context=4096,
            data_dir=Path(td),
            skip_characters=True,
        )
        args = _args(
            adventure=None,
            compose_modules=True,
            module_start="library_research",
            module_max_depth=4,
            module_seed=42,
        )

        adventure = cli._load_requested_adventure(session, args)

        assert adventure is not None
        assert adventure.id.startswith("composed_library_research_")
        assert session.state.adventure_id == adventure.id
        assert session.state.scene_id == "library_research::library"

        exits = adventure.scene_exits("library_research::library_converge", include_locked=True)
        targets = {
            adventure.get_scene(exit_info.target_id).leads_to[0]
            for exit_info in exits
            if adventure.get_scene(exit_info.target_id) is not None
            and adventure.get_scene(exit_info.target_id).leads_to
        }
        # 手写目标必须始终保留；组合引擎现在还会叠加随机兼容候选作为额外分支。
        assert {
            "basement_confrontation::basement",
            "sanitarium_visit::ward14",
        } <= targets


def test_run_cli_resumes_saved_adventure(monkeypatch, capsys):
    DummyClient.created.clear()
    DummyClient.closed = 0

    class ResumeSession(DummySession):
        def __init__(self, session_id: str, *, max_context: int):
            super().__init__(session_id, max_context=max_context)
            self.state.adventure_id = "haunted_house"

    async def fake_read(_prompt: str) -> str:
        return "/quit"

    monkeypatch.setattr(cli, "Session", ResumeSession)
    monkeypatch.setattr(cli, "OllamaClient", DummyClient)
    monkeypatch.setattr(cli, "_read_user_input", fake_read)
    monkeypatch.setattr(cli, "DEFAULT_PREFLIGHT", False)

    rc = asyncio.run(cli._run_cli(_args(adventure=None)))

    assert rc == 0
    out = capsys.readouterr().out
    assert "模组: 已恢复模组 (saved_adv)" in out