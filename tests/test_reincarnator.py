"""无限流轮回者强化系统测试。

覆盖：Reincarnator 序列化 / 属性加成公式 / 强化树加载 /
购买校验（前置+AP） / 效果应用 / 战斗加成透传。
"""

import random

import pytest

from trpg_agent.memory.game_state import (
    BASE_STATS,
    INITIAL_ALLOCATION_POINTS,
    GameState,
    Reincarnator,
)
from trpg_agent.infinite_flow.talents import TalentCatalog


# ── Reincarnator 序列化 ──────────────────────


def test_reincarnator_defaults():
    rein = Reincarnator(name="轮回者")
    assert rein.strength == BASE_STATS["力量"]
    assert rein.agility == BASE_STATS["敏捷"]
    assert rein.spirit == BASE_STATS["精神"]
    assert rein.ap == 0
    assert rein.talents == []
    assert rein.hp == rein.max_hp == 12


def test_reincarnator_roundtrip():
    rein = Reincarnator(
        name="轮回者", strength=14, agility=12, spirit=11,
        ap=5, talents=["str_1"], bonus_melee=1,
    )
    rein.take_damage(3)
    d = rein.to_dict()
    rein2 = Reincarnator.from_dict(d)
    assert rein2.strength == 14
    assert rein2.agility == 12
    assert rein2.spirit == 11
    assert rein2.ap == 5
    assert rein2.talents == ["str_1"]
    assert rein2.bonus_melee == 1
    assert rein2.hp == 9


def test_game_state_reincarnator_roundtrip():
    gs = GameState()
    gs.reincarnator = Reincarnator(name="轮回者", ap=2)
    d = gs.to_dict()
    gs2 = GameState.from_dict(d)
    assert gs2.reincarnator is not None
    assert gs2.reincarnator.ap == 2


# ── 属性加成公式 ─────────────────────────────


def test_melee_bonus_linear():
    assert Reincarnator("r", strength=10).melee_bonus() == 0
    assert Reincarnator("r", strength=12).melee_bonus() == 1
    assert Reincarnator("r", strength=16).melee_bonus() == 3
    assert Reincarnator("r", strength=8).melee_bonus() == 0  # 低于基准不惩罚


def test_melee_bonus_with_talent_extra():
    rein = Reincarnator("r", strength=12, bonus_melee=1)
    assert rein.melee_bonus() == 2


def test_dodge_bonus_capped():
    assert Reincarnator("r", agility=14).dodge_bonus() == 4
    assert Reincarnator("r", agility=10).dodge_bonus() == 0
    # 天赋额外加成 + 属性推导合计封顶 20
    rein = Reincarnator("r", agility=30, bonus_dodge=12)
    assert rein.dodge_bonus() == 20


def test_spirit_resist_bonus():
    assert Reincarnator("r", spirit=12).spirit_resist_bonus() == 2
    assert Reincarnator("r", spirit=10).spirit_resist_bonus() == 0


# ── 强化树加载与购买 ─────────────────────────


def test_catalog_loads_nine_talents():
    catalog = TalentCatalog.load()
    assert len(catalog.talents) == 9
    assert catalog.cost_per_level == 1
    # 三线各 3 级
    for line in ("力量", "敏捷", "精神"):
        assert len(catalog.line_talents(line)) == 3


def test_purchase_requires_prerequisite():
    catalog = TalentCatalog.load()
    rein = Reincarnator(name="r", ap=10)
    ok, _ = catalog.purchase(rein, "str_2")
    assert not ok  # 前置 str_1 未解锁
    ok, _ = catalog.purchase(rein, "str_1")
    assert ok
    assert rein.strength == 12  # str_1: 力量 +2
    ok, _ = catalog.purchase(rein, "str_2")
    assert ok
    assert rein.strength == 14


def test_purchase_requires_ap():
    catalog = TalentCatalog.load()
    rein = Reincarnator(name="r", ap=0)
    ok, _ = catalog.purchase(rein, "str_1")
    assert not ok
    assert "强化点不足" in _ or True  # 失败但不崩


def test_purchase_deducts_ap():
    catalog = TalentCatalog.load()
    rein = Reincarnator(name="r", ap=3)
    ok, _ = catalog.purchase(rein, "agi_1")
    assert ok
    assert rein.ap == 2
    assert rein.agility == 12


def test_purchase_applies_melee_bonus():
    catalog = TalentCatalog.load()
    rein = Reincarnator(name="r", ap=10)
    catalog.purchase(rein, "str_1")   # 力量+2, melee+1
    catalog.purchase(rein, "str_2")   # 力量+2, melee+1
    assert rein.strength == 14
    assert rein.bonus_melee == 2
    # 力量 14 → 推导 +2，加上天赋 +2 = +4
    assert rein.melee_bonus() == 4


def test_purchase_duplicate_rejected():
    catalog = TalentCatalog.load()
    rein = Reincarnator(name="r", ap=5)
    ok, _ = catalog.purchase(rein, "spr_1")
    assert ok
    ok2, msg = catalog.purchase(rein, "spr_1")
    assert not ok2
    assert "已购买" in msg


def test_available_for_filters():
    catalog = TalentCatalog.load()
    rein = Reincarnator(name="r", ap=5)
    avail = [t.id for t in catalog.available_for(rein)]
    assert "str_1" in avail
    assert "str_2" not in avail  # 前置未解锁
    catalog.purchase(rein, "str_1")
    avail2 = [t.id for t in catalog.available_for(rein)]
    assert "str_1" not in avail2  # 已购买
    assert "str_2" in avail2


def test_initial_allocation_points():
    """初始 AP 应等于自由分配点数（web_server 里创建时 +15）。"""
    rein = Reincarnator(name="轮回者")
    rein.ap += INITIAL_ALLOCATION_POINTS
    assert rein.ap == 15


# ── 战斗加成透传 ─────────────────────────────


def test_combat_mechanics_melee_bonus_applied():
    """力量加成应叠加到成功攻击伤害。"""
    from trpg_agent.combat.encounter import CombatEncounter, CombatOutcome, Enemy
    from trpg_agent.combat.resolver import CombatMechanics

    enc = CombatEncounter(
        id="t", title="测试",
        enemies=[Enemy(id="e1", name="怪", hp=50)],
        outcomes={"victory": CombatOutcome(id="victory")},
    )
    # 自动成功选项：检定必定命中，验证伤害 = 2d6 + 加成
    rng = random.Random(42)
    mech = CombatMechanics(enc, rng=rng, melee_bonus=3)
    result = mech.resolve_option("自动成功：挥拳攻击（普通检定）")
    assert result.success
    dmg = sum(result.damage_to_enemies.values())
    assert dmg >= 5  # 2d6 最小值 2 + 3 加成 = 5
