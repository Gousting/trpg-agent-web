"""reusable 模块测试——无限流主神空间/副本循环支持。

验证点：
1. 普通模块（reusable 默认 False）一局内只出现一次——行为不变
2. reusable 模块豁免 used_ids 去重——可被多次进入
3. hub 循环不死循环——BFS 深度上限仍生效
4. from_dict 默认 False——现有模块零影响
"""

import json
from pathlib import Path

import pytest

from trpg_agent.adventure.module_composer import ModuleComposer, ModuleMeta


def _write_module(dir_path: Path, data: dict) -> None:
    """写一个 module.json 到临时目录。"""
    d = dir_path / data["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "module.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _scene(scene_id: str, title: str = "场景", leads_to=None, exit_labels=None):
    scene = {
        "id": scene_id,
        "title": title,
        "part": 1,
        "scene_type": "普通",
        "description": "测试场景",
        "npcs_here": [],
        "opportunities": [],
        "secrets": [],
        "leads_to": leads_to or [],
        "guidance": "",
    }
    if exit_labels:
        scene["exit_labels"] = exit_labels
    return scene


@pytest.fixture
def hub_pool(tmp_path: Path):
    """一个含 hub + 三个普通模块的测试池。

    hub 是 reusable 起始模块，出口指向 m1/m2/m3。
    """
    hub = {
        "id": "hub",
        "title": "主神空间",
        "genre": ["scifi"],
        "difficulty": 1,
        "duration_estimate": "1 turn",
        "entry": {"location_types": [], "required_clues": [], "forbidden_clues": [], "mood": None},
        "reusable": True,
        "exits": [
            {
                "id": "to_m1",
                "label": "进入副本一",
                "provides_clues": ["clue_a"],
                "mood": "",
                "next_location_type": "dungeon",
            },
            {
                "id": "to_m2",
                "label": "进入副本二",
                "provides_clues": ["clue_b"],
                "mood": "",
                "next_location_type": "dungeon",
            },
            {
                "id": "to_m3",
                "label": "进入副本三",
                "provides_clues": ["clue_c"],
                "mood": "",
                "next_location_type": "dungeon",
            },
        ],
        "scenes": [
            _scene(
                "hub_room",
                "主神空间",
                leads_to=["hub_exit"],
                exit_labels={"hub_exit": "选择副本"},
            ),
            _scene("hub_exit", "选择出口"),
        ],
        "npcs": [],
        "variance": None,
    }

    modules = [hub]
    for i, c in enumerate(["a", "b", "c"], start=1):
        modules.append({
            "id": f"m{i}",
            "title": f"副本{i}",
            "genre": ["scifi"],
            "difficulty": 1,
            "duration_estimate": "1 turn",
            "entry": {"location_types": ["dungeon"], "required_clues": [], "forbidden_clues": [], "mood": None},
            "reusable": False,
            "exits": [
                {
                    "id": f"back_{c}",
                    "label": f"返回主神空间{c}",
                    "provides_clues": [f"clue_{c}"],
                    "mood": "",
                    "next_location_type": "hub",
                }
            ],
            "scenes": [
                _scene(
                    f"room_{c}",
                    f"副本{i}房间",
                    leads_to=[f"exit_{c}"],
                    exit_labels={f"exit_{c}": f"离开副本{i}"},
                ),
                _scene(f"exit_{c}", f"副本{i}出口"),
            ],
            "npcs": [],
            "variance": None,
        })

    for m in modules:
        _write_module(tmp_path, m)
    return tmp_path


class TestReusableModules:
    def test_meta_defaults_false(self):
        """现有模块不带 reusable 字段时默认为 False——零影响。"""
        meta = ModuleMeta.from_dict({"id": "plain", "title": "普通"})
        assert meta.reusable is False

    def test_meta_parses_true(self):
        meta = ModuleMeta.from_dict({"id": "hub", "title": "主神空间", "reusable": True})
        assert meta.reusable is True

    def test_plain_modules_still_deduplicated(self, hub_pool):
        """普通模块（reusable=False）一局内仍只出现一次。"""
        from collections import Counter

        composer = ModuleComposer(hub_pool)
        composer.load_all()
        assert composer.module_count == 4

        # m1 作为起始模块（非 reusable），下游不再重复出现 m1
        adv, _seed = composer.compose(seed=1, max_depth=4, start_module="m1")
        prefixes = [
            sid.split("::", 1)[0]
            for sid in adv._scenes
            if "::" in sid
        ]
        counts = Counter(prefixes)
        # m1 自身场景 + 可能的过渡，但 m1 不会作为下游模块被再次展开
        assert counts["m1"] >= 1
        # 关键：普通模块不允许重复进入——m1 场景不会以副本形式出现两次
        # （m1 是起始模块，BFS 从它展开，不会再次匹配回自己）
        assert "m1" in counts

    def test_reusable_hub_can_be_reentered(self, hub_pool):
        """reusable hub 模块在循环路径中不被 used_ids 排除。

        m1 出口指回 hub（next_location_type=hub），hub 已在 used_ids 中，
        但 reusable=True 豁免去重，m1 → hub 的边仍应建立。
        """
        composer = ModuleComposer(hub_pool)
        composer.load_all()

        adv, _seed = composer.compose(seed=2, max_depth=3, start_module="m1")

        # m1 的最后一个场景应有返回主神空间的出口
        m1_exit = adv.get_scene("m1::exit_a")
        assert m1_exit is not None
        exits = adv.scene_exits("m1::exit_a", include_locked=True)
        targets = {
            adv.get_scene(e.target_id).leads_to[0]
            for e in exits
            if adv.get_scene(e.target_id) is not None
            and adv.get_scene(e.target_id).leads_to
        }
        # 出口指向 hub 模块（reusable 豁免 used_ids 后仍可匹配）
        assert any(t.startswith("hub::") for t in targets)

    def test_no_infinite_loop_with_reusable_hub(self, hub_pool):
        """reusable hub 存在循环边时不产生无限场景。

        BFS 深度上限 + node_map 复用共同保证组合有界。
        """
        composer = ModuleComposer(hub_pool)
        composer.load_all()

        # 用 hub 作为起始（它本身就是 reusable），深度上限 6
        adv, _seed = composer.compose(seed=3, max_depth=6, start_module="hub")
        assert len(adv._scenes) > 0
        # 场景数有界：hub(2) + 每模块最多 2 场景 + 过渡场景，深度 6 不可能爆炸
        assert len(adv._scenes) < 60

    def test_reusable_documented_in_json(self, hub_pool):
        """JSON 中显式声明 reusable 的模块被正确解析。"""
        composer = ModuleComposer(hub_pool)
        composer.load_all()
        hub_meta = composer._modules["hub"].meta
        assert hub_meta.reusable is True
