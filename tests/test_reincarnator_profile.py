"""T6：轮回者存档——profiles 读写测试。

验证点：
1. _save_reincarnator 写入 profiles/<name>.json（roundtrip 一致）
2. _load_reincarnator 读档还原属性/AP/强化
3. 无存档返回 None
4. 损坏存档容错返回 None
"""
import json
import re

import pytest

from trpg_agent.memory.game_state import Reincarnator
from trpg_agent_web import web_server


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "PROFILE_DIR", tmp_path)
    return tmp_path


def _make_rein(name: str = "轮回者", ap: int = 7) -> Reincarnator:
    rein = Reincarnator(name=name, max_hp=12, hp=9, strength=14, agility=12, spirit=11, ap=ap)
    rein.talents = ["str_1", "agi_1"]
    rein.conditions = ["受伤"]
    return rein


class TestReincarnatorProfile:
    def test_save_then_load_roundtrip(self, profile_dir):
        rein = _make_rein()
        web_server._save_reincarnator(rein)
        path = profile_dir / "轮回者.json"
        assert path.is_file(), f"存档文件未生成: {path}"

        loaded = web_server._load_reincarnator()
        assert loaded is not None
        assert loaded.strength == 14
        assert loaded.agility == 12
        assert loaded.spirit == 11
        assert loaded.ap == 7
        assert loaded.talents == ["str_1", "agi_1"]
        assert loaded.conditions == ["受伤"]
        assert loaded.hp == 9

    def test_save_json_has_key_fields(self, profile_dir):
        web_server._save_reincarnator(_make_rein())
        data = json.loads((profile_dir / "轮回者.json").read_text(encoding="utf-8"))
        for key in ("name", "hp", "max_hp", "strength", "agility", "spirit", "ap", "talents"):
            assert key in data, f"存档缺少字段 {key}"

    def test_load_missing_profile_returns_none(self, profile_dir):
        assert web_server._load_reincarnator() is None

    def test_load_corrupted_profile_returns_none(self, profile_dir):
        (profile_dir / "轮回者.json").write_text("{ broken json", encoding="utf-8")
        assert web_server._load_reincarnator() is None

    def test_custom_name(self, profile_dir):
        web_server._save_reincarnator(_make_rein(name="阿伟"))
        assert (profile_dir / "阿伟.json").is_file()
        loaded = web_server._load_reincarnator("阿伟")
        assert loaded is not None and loaded.name == "阿伟"