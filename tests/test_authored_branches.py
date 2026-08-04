"""T1：出口机制——authored target 分支可达性测试。

验证点：
1. 模块最后场景 exit_labels 手写目标 → 显式边（不随机匹配），投票选项直接映射目标场景
2. 每场景可声明 3+ 个真实出口（max_authored_branches=None 不限，默认放开）
3. max_authored_branches=N 时缩减到 N 个
4. 撤退出口：authored target 指向 hub 模块可回主神空间
5. 不串线：池子中有兼容随机候选也不连接未声明的模块
"""
import json

import pytest

from trpg_agent.adventure.module_composer import ModuleComposer


def _module_json(
    mid: str,
    location_types: list[str],
    *,
    required_clues: list[str] | None = None,
    forbidden_clues: list[str] | None = None,
    exits: list[dict] | None = None,
    exit_labels: dict[str, str] | None = None,
    is_ending: bool = False,
    reusable: bool = False,
) -> dict:
    """构造最小模块 JSON（单场景叙事模块）。"""
    return {
        "id": mid,
        "title": mid,
        "genre": ["test"],
        "difficulty": 1,
        "reusable": reusable,
        "is_ending": is_ending,
        "entry": {
            "location_types": location_types,
            "required_clues": required_clues or [],
            "forbidden_clues": forbidden_clues or [],
        },
        "exits": exits or [],
        "scenes": [
            {
                "id": "s1",
                "title": f"{mid} 场景",
                "part": 0,
                "description": f"{mid} 的测试场景",
                "exit_labels": exit_labels or {},
            }
        ],
    }


def _write_pool(tmp_path, modules: list[dict]) -> ModuleComposer:
    for mod in modules:
        d = tmp_path / mod["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "module.json").write_text(json.dumps(mod, ensure_ascii=False), encoding="utf-8")
    composer = ModuleComposer(tmp_path)
    composer.load_all()
    return composer


def _trans_targets(adv, scene_id: str) -> list[str]:
    """返回场景所有出口（过渡场景）真正指向的目标场景 id。"""
    targets = []
    for e in adv.scene_exits(scene_id, include_locked=True):
        trans = adv.get_scene(e.target_id)
        if trans and trans.leads_to:
            targets.append(trans.leads_to[0])
    return targets


class TestAuthoredBranches:
    def test_two_authored_exits_are_both_reachable(self, tmp_path):
        """A 手写 B/C 两个出口 → 编译后 A 有两条边，投票选项映射到 B/C 第一场景。"""
        pool = [
            _module_json("a", ["loc"], exits=[{"id": "e1", "label": "前进", "next_location_type": "loc"}],
                         exit_labels={"b::s1": "去 B 路线", "c::s1": "去 C 路线"}),
            _module_json("b", ["loc"], is_ending=True),
            _module_json("c", ["loc"], is_ending=True),
        ]
        composer = _write_pool(tmp_path, pool)
        adv, _seed = composer.compose(seed=1, max_depth=2, start_module="a")

        exits = adv.scene_exits("a::s1", include_locked=True)
        assert len(exits) == 2, f"期望 2 个出口，实际 {len(exits)}"
        labels = {e.label for e in exits}
        assert labels == {"去 B 路线", "去 C 路线"}, f"投票选项 label 错误: {labels}"

        targets = set(_trans_targets(adv, "a::s1"))
        assert targets == {"b::s1", "c::s1"}, f"出口未指向声明的目标: {targets}"

    def test_three_authored_exits_not_truncated_by_default(self, tmp_path):
        """A 手写 B/C/D 三个出口 → 默认（max_authored_branches=None）不缩减，3 条边都在。"""
        pool = [
            _module_json("a", ["loc"], exits=[{"id": "e1", "label": "前进", "next_location_type": "loc"}],
                         exit_labels={"b::s1": "去 B", "c::s1": "去 C", "d::s1": "去 D"}),
            _module_json("b", ["loc"], is_ending=True),
            _module_json("c", ["loc"], is_ending=True),
            _module_json("d", ["loc"], is_ending=True),
        ]
        composer = _write_pool(tmp_path, pool)
        adv, _seed = composer.compose(seed=1, max_depth=2, start_module="a")

        targets = set(_trans_targets(adv, "a::s1"))
        assert targets == {"b::s1", "c::s1", "d::s1"}, f"3 出口应全部保留: {targets}"

    def test_max_authored_branches_truncates(self, tmp_path):
        """显式传 max_authored_branches=2 → 从 3 个目标缩减到 2 个（兼容旧行为）。"""
        pool = [
            _module_json("a", ["loc"], exits=[{"id": "e1", "label": "前进", "next_location_type": "loc"}],
                         exit_labels={"b::s1": "去 B", "c::s1": "去 C", "d::s1": "去 D"}),
            _module_json("b", ["loc"], is_ending=True),
            _module_json("c", ["loc"], is_ending=True),
            _module_json("d", ["loc"], is_ending=True),
        ]
        composer = _write_pool(tmp_path, pool)
        adv, _seed = composer.compose(seed=1, max_depth=2, start_module="a", max_authored_branches=2)

        targets = set(_trans_targets(adv, "a::s1"))
        assert len(targets) == 2, f"应缩减到 2 个出口: {targets}"
        assert targets <= {"b::s1", "c::s1", "d::s1"}

    def test_retreat_exit_to_hub(self, tmp_path):
        """A 手写撤退出口指向 hub → 边连接 hub（主神空间循环）。"""
        pool = [
            _module_json("hub", ["hub"], reusable=True,
                         exit_labels={}),
            _module_json("a", ["loc"], exits=[{"id": "e1", "label": "深入", "next_location_type": "loc"}],
                         exit_labels={"hub::s1": "撤退回主神空间"}),
        ]
        composer = _write_pool(tmp_path, pool)
        adv, _seed = composer.compose(seed=1, max_depth=2, start_module="a")

        exits = adv.scene_exits("a::s1", include_locked=True)
        labels = {e.label for e in exits}
        assert "撤退回主神空间" in labels, f"撤退出口缺失: {labels}"
        targets = set(_trans_targets(adv, "a::s1"))
        assert "hub::s1" in targets, f"撤退未指向 hub: {targets}"

    def test_no_cross_wiring_with_compatible_candidates(self, tmp_path):
        """池子中有兼容的随机候选（同 location_type）时，authored 只连接声明的目标。"""
        pool = [
            _module_json("a", ["loc"], exits=[{"id": "e1", "label": "前进", "next_location_type": "loc"}],
                         exit_labels={"b::s1": "去 B"}),
            _module_json("b", ["loc"], is_ending=True),
            # x 与 a 同 location_type，兼容随机匹配，但 a 未声明指向它
            _module_json("x", ["loc"], is_ending=True),
        ]
        composer = _write_pool(tmp_path, pool)
        adv, _seed = composer.compose(seed=1, max_depth=2, start_module="a", authored_only=True)

        targets = set(_trans_targets(adv, "a::s1"))
        assert targets == {"b::s1"}, f"不应连接到未声明的 x: {targets}"
        assert "x::s1" not in targets
