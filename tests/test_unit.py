"""单元测试 — 非 LLM 组件的完整覆盖。

用法: uv run pytest tests/test_unit.py -v
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.llm.sanitize import (
    _sanitize,
    _sanitize_leading,
    _sanitize_trailing,
    _cut_at_labels,
    _strip_leading_label,
    _trim_to_last_sentence,
    _ROLE_LABELS,
)
from trpg_agent.llm.persona import load_system_prompt
from trpg_agent.llm.prompt_assembly import assemble_system_prompt
from trpg_agent.rules.engine import roll
from trpg_agent.rules.coc import (
    resolve_coc,
    describe_result,
    SuccessLevel,
    _effective_target,
    _is_fumble,
)


# ═══════════════════════════════════════════════════════════════
# Sanitize 测试
# ═══════════════════════════════════════════════════════════════

class TestSanitize:
    """KP 回答清洗管道"""

    def test_strip_role_label_kp(self):
        assert _sanitize("守秘人：你们走进一间昏暗的书房。") == "你们走进一间昏暗的书房。"

    def test_strip_role_label_kp_en(self):
        assert _sanitize("KP：黑暗的走廊里传来一阵低语。") == "黑暗的走廊里传来一阵低语。"

    def test_strip_role_label_gm(self):
        assert _sanitize("GM：The door creaks open.") == "The door creaks open."

    def test_strip_meta_preamble_as_kp(self):
        r = _sanitize("作为守秘人，我描述一下当前的场景：你们站在一座废弃的教堂前。")
        assert "作为守秘人" not in r
        assert "你们站在" in r or "教堂" in r

    def test_strip_meta_preamble_let_me(self):
        r = _sanitize("好的，让我为你描述一下：这是一间布满灰尘的档案室。")
        assert "好的" not in r
        assert "档案室" in r

    def test_strip_trailing_prompt(self):
        r = _sanitize("地下室传来低沉的嘶吼声。你们要怎么做？")
        assert "你们要怎么做" not in r
        assert "嘶吼" in r

    def test_strip_trailing_prompt_please(self):
        r = _sanitize("门后是一条石阶。请做出你的选择。")
        assert "请做出" not in r

    def test_strip_meta_paren(self):
        r = _sanitize("走廊尽头有扇铁门。（请注意，作为KP我不会替调查员做决定）")
        assert "请注意" not in r

    def test_strip_ai_selfref(self):
        r = _sanitize("作为一个AI语言模型，我建议你检查一下。但其实你应该自己决定。")
        assert "AI语言模型" not in r
        assert "自己决定" in r

    def test_strip_self_correct(self):
        r = _sanitize("以下是正确的回答：你们推开沉重的橡木门，一股霉味扑面而来。")
        assert "以下是正确的回答" not in r
        assert "橡木门" in r

    def test_strip_transitional(self):
        r = _sanitize_leading("首先，你们注意到书桌上散落着几页泛黄的手稿。")
        assert "首先" not in r

    def test_normal_narration_untouched(self):
        original = "老神父抬起头，浑浊的眼睛里闪过一丝恐惧：「你们不该来这里的。」"
        r = _sanitize(original)
        assert "你们不该来这里" in r

    def test_cut_at_labels(self):
        text = "你打开抽屉，里面有一把生锈的钥匙。玩家：我要拿钥匙。"
        r = _cut_at_labels(text, _ROLE_LABELS)
        assert "玩家" not in r
        assert "生锈的钥匙" in r

    def test_strip_leading_label(self):
        r = _strip_leading_label("守秘人：你好", _ROLE_LABELS)
        assert r == "你好"

    def test_trim_to_last_sentence_complete(self):
        text = "这是一间狭小的房间。墙壁上挂着一面镜子。"
        assert _trim_to_last_sentence(text) == text

    def test_trim_to_last_sentence_truncated(self):
        r = _trim_to_last_sentence("这是一间狭小的房间。墙壁上挂着一面镜")
        assert r == "这是一间狭小的房间。"

    def test_trim_no_punctuation(self):
        text = "黑暗中你摸索着前行"
        assert _trim_to_last_sentence(text) == text  # nothing to fall back to

    def test_enclosing_quotes_chinese(self):
        r = _sanitize("「你们推开沉重的铁门，一股霉味扑面而来。」")
        assert "「" not in r
        assert "铁门" in r

    def test_enclosing_quotes_nested_survives(self):
        original = '老人说：「你们不该来这里的。」然后他转身消失在阴影中。'
        r = _sanitize(original)
        # The outer quote check: first char is not a quote, so it should pass through
        assert "不该来这里" in r


# ═══════════════════════════════════════════════════════════════
# Persona + Prompt Assembly 测试
# ═══════════════════════════════════════════════════════════════

class TestPersona:
    """KP 人格加载"""

    def test_load_core_prompt(self):
        prompt = load_system_prompt()
        assert len(prompt) > 1000
        assert "守秘人" in prompt
        assert "调查员" in prompt

    def test_prompt_assembly_basic(self):
        result = assemble_system_prompt(persona="核心人格")
        assert result == "核心人格"

    def test_prompt_assembly_with_recap(self):
        result = assemble_system_prompt(
            persona="核心人格",
            recap="调查员进入了旧图书馆。",
        )
        assert "前情提要" in result
        assert "调查员已知内容" in result
        assert "旧图书馆" in result

    def test_prompt_assembly_skips_empty(self):
        result = assemble_system_prompt(
            persona="核心",
            recap="",
            state_summary=None,
            npc_memory="",
            rag=None,
            alias_hint="玩家A=陈明",
        )
        assert "核心" in result
        assert "玩家A=陈明" in result

    def test_prompt_assembly_full(self):
        result = assemble_system_prompt(
            persona="核心人格",
            recap="前情",
            adventure="冒险摘要",
            state_summary="状态",
            npc_memory="NPC记忆",
            rag="规则检索",
            alias_hint="别名",
        )
        # 按顺序检查各层都在
        idx_persona = result.index("核心人格")
        idx_recap = result.index("前情提要")
        idx_adv = result.index("冒险摘要")
        idx_state = result.index("状态")
        idx_npc = result.index("NPC记忆")
        idx_rag = result.index("规则检索")
        idx_alias = result.index("别名")
        assert idx_persona < idx_recap < idx_adv < idx_state < idx_npc < idx_rag < idx_alias


# ═══════════════════════════════════════════════════════════════
# COC 检定引擎测试
# ═══════════════════════════════════════════════════════════════

class TestCocEngine:
    """COC 7 版检定"""

    def test_effective_target_regular(self):
        assert _effective_target(60, "常规") == 60

    def test_effective_target_hard(self):
        assert _effective_target(60, "困难") == 30
        assert _effective_target(61, "困难") == 30  # floor division
        assert _effective_target(1, "困难") == 1     # minimum is 1

    def test_effective_target_extreme(self):
        assert _effective_target(60, "极难") == 12
        assert _effective_target(100, "极难") == 20

    def test_is_fumble_low_skill(self):
        assert _is_fumble(96, 30) is True
        assert _is_fumble(100, 30) is True
        assert _is_fumble(95, 30) is False

    def test_is_fumble_high_skill(self):
        assert _is_fumble(100, 60) is True
        assert _is_fumble(99, 60) is False
        assert _is_fumble(96, 60) is False

    def test_resolve_regular_success(self):
        rng = random.Random(42)
        result = resolve_coc(50, "常规", rng=rng)
        assert result.skill_value == 50
        assert result.target == 50
        assert result.difficulty == "常规"

    def test_resolve_hard_target(self):
        rng = random.Random(42)
        result = resolve_coc(60, "困难", rng=rng)
        assert result.target == 30

    def test_resolve_extreme_target(self):
        rng = random.Random(42)
        result = resolve_coc(80, "极难", rng=rng)
        assert result.target == 16

    def test_describe_result(self):
        rng = random.Random(99)
        result = resolve_coc(50, "常规", rng=rng)
        desc = describe_result(result, "侦查")
        assert "侦查" in desc
        assert "骰值" in desc

    def test_success_levels_mutually_exclusive(self):
        """大成功和大失败不可能同时为真"""
        rng = random.Random()
        for _ in range(100):
            result = resolve_coc(50, "常规", rng=rng)
            assert not (result.is_critical and result.is_fumble)

    def test_success_implies_not_failure(self):
        rng = random.Random()
        for _ in range(50):
            result = resolve_coc(50, "常规", rng=rng)
            if result.success:
                assert not result.is_fumble
                assert result.level != SuccessLevel.FAILURE


# ═══════════════════════════════════════════════════════════════
# 骰子引擎测试
# ═══════════════════════════════════════════════════════════════

class TestDiceEngine:
    """通用掷骰"""

    def test_d100_range(self):
        rng = random.Random()
        for _ in range(100):
            r = roll("1d100", rng)
            assert 1 <= r.total <= 100

    def test_multi_dice(self):
        rng = random.Random(42)
        r = roll("3d6", rng)
        assert len(r.dice) == 3
        assert all(1 <= d <= 6 for d in r.dice)
        assert r.total == sum(r.dice)

    def test_with_modifier(self):
        rng = random.Random(42)
        r = roll("2d10+5", rng)
        assert r.modifier == 5
        assert r.total == sum(r.dice) + 5

    def test_constant(self):
        r = roll("42")
        assert r.total == 42
        assert r.dice == ()
        assert r.modifier == 42
