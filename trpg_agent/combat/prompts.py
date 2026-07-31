"""战斗场景的 LLM Prompt 模板 —— 赌注式选项 + 逐轮升级。

每回合 LLM 需要看到完整的遭遇数据、当前战场状态、已发生的叙事历史，
生成一个叙事节拍 + 三个带代价预览的选项。

设计原则：
- 不是菜单（"攻击/利用环境/特殊行动"），是赌注（"你可以X，但会付出Y"）
- 每个选项必须明确代价：HP损失、SAN检定、永久后果、队友风险
- 选项之间必须有真正的取舍——没有"明显正确"的选项
- 回合升级：每轮环境变得更危险，制造时间压力
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def build_combat_system_prompt(encounter, *, round_number: int = 1, escalation: str = "") -> str:
    """构造战斗场景的系统提示词 —— 赌注式选项生成。

    参数:
        encounter: CombatEncounter 实例
        round_number: 当前回合数（第1轮 = 开战叙事）
        escalation: 当前轮的升级叙事（环境恶化、新威胁出现等）

    返回:
        插入到 system message 中的战斗规则文本
    """
    from trpg_agent.combat.encounter import CombatEncounter  # noqa: F811

    lines = ["[战斗模式]", ""]

    # ── 敌人面板 ──
    lines.append("## 敌人")
    for enemy in encounter.enemies:
        count_str = f" ×{enemy.count}" if enemy.count > 1 else ""
        status = f"HP:{enemy.hp}/{enemy.hp_max}" if enemy.hp_max > 0 else f"HP:{enemy.hp}"
        lines.append(f"- **{enemy.name}**{count_str}  {status}  护甲:{enemy.armor}")
        if enemy.abilities:
            for ab in enemy.abilities:
                name = ab.get("name", "")
                effect = ab.get("effect", "")
                if name:
                    lines.append(f"  - {name}：{effect}")
        if enemy.behavior:
            lines.append(f"  - 行为模式：{enemy.behavior}")
    lines.append("")

    # ── 环境 ──
    env = encounter.environment
    if env.terrain:
        lines.append(f"## 环境：{env.terrain}")
    if env.lighting and env.lighting != "normal":
        light_desc = {
            "dim": "昏暗（所有攻击 -1）",
            "dark": "完全黑暗（无法瞄准，需光源）",
            "bioluminescent": "幽暗的荧光照明（视野受限，远处细节模糊）",
        }
        lines.append(f"- 光照：{light_desc.get(env.lighting, env.lighting)}")
    if env.hazards:
        lines.append("- 可互动的环境要素：")
        for h in env.hazards:
            lines.append(f"  - {h}")
    lines.append("")

    # ── 特殊规则 ──
    if encounter.special_rules:
        lines.append("## 特殊规则")
        for r in encounter.special_rules:
            lines.append(f"- {r}")
        lines.append("")

    # ── 逐轮升级 ──
    if escalation:
        lines.append(f"## ⚠️ 局势升级（第{round_number}轮）")
        lines.append(escalation)
        lines.append("")

    # ── 选项生成规则 ──
    lines.append("## 你的任务：生成本回合的叙事 + 三个选项")
    lines.append("")
    lines.append("### 第一步：开场叙事（1-2句话）")
    lines.append("描述本回合开始时战场上的紧张瞬间——不要回顾历史，直击当下的威胁。")
    lines.append("把聚光灯打在一个具体的画面上：敌人的动作、环境的变化、某个调查员的处境。")
    lines.append("")
    lines.append("### 第二步：生成三个赌注式选项")
    lines.append("**每条选项必须包含明确的代价/风险/后果预览。**")
    lines.append("不要写'攻击敌人'——写'正面迎战长老，但你需要通过蛙鸣咆哮的 SAN 检定（1d3/1d6）'。")
    lines.append("不要写'利用环境'——写'打碎祭坛削弱长老（攻击-2，1d3回合），但浮雕内容让全队 SAN-1，且长老会优先攻击破坏者'。")
    lines.append("")
    lines.append("选项设计铁律：")
    lines.append("1. **每条选项必须有代价**——HP 损失、SAN 检定、队友受伤、永久后果、时间消耗")
    lines.append("2. **三条选项必须是真正的取舍**——不存在明显最优解，每条路都有独特的痛")
    lines.append("3. **覆盖不同维度**——进攻、防守、环境利用、牺牲、谈判、逃跑")
    lines.append("4. **利用环境和特殊规则**——不要生成和场景无关的通用选项")
    lines.append("5. **代价量化**——不要说'可能受伤'，说'DEX 检定（困难），失败则受到 1d6 坠落伤害'")
    lines.append("")
    lines.append("### 输出格式")
    lines.append("先写开场叙事段落，然后空一行，输出三条选项，每条用 --- 分隔：")
    lines.append("")
    lines.append("（开场叙事——1-2句话，直击当下的紧张瞬间）")
    lines.append("")
    lines.append("---")
    lines.append("**选项A标题**")
    lines.append("行动描述。代价：xxx。成功条件：xx检定（难度）/ 自动成功。")
    lines.append("---")
    lines.append("**选项B标题**")
    lines.append("行动描述。代价：xxx。成功条件：xx检定（难度）/ 自动成功。")
    lines.append("---")
    lines.append("**选项C标题**")
    lines.append("行动描述。代价：xxx。成功条件：xx检定（难度）/ 自动成功。")

    return "\n".join(lines)


def build_combat_turn_user_prompt(
    encounter,
    *,
    round_number: int,
    round_history: list[str],
    investigators_state: str,
    escalation: str = "",
) -> str:
    """构造当前回合的 user 消息。

    参数:
        encounter: CombatEncounter 实例
        round_number: 当前回合数
        round_history: 前几回合的叙事摘要列表（[第1轮结果, 第2轮结果, ...]）
        investigators_state: 调查员当前状态文本
        escalation: 本轮的升级叙事

    返回:
        user 消息文本（发送给 LLM）
    """
    parts = [f"—— 第 {round_number} 回合 ——", ""]

    # 历史回顾
    if round_history:
        parts.append("## 已发生的战斗过程")
        for i, summary in enumerate(round_history, 1):
            parts.append(f"第{i}轮：{summary}")
        parts.append("")

    # 当前状态
    if escalation:
        parts.append(f"## 局势变化\n{escalation}")
        parts.append("")

    parts.append("## 当前敌人状态")
    alive = [e for e in encounter.enemies if e.is_alive()]
    if alive:
        for e in alive:
            parts.append(f"- {e.name} HP:{e.hp}/{e.hp_max}")
    else:
        parts.append("- 所有敌人已被击倒")
    parts.append("")

    if investigators_state:
        parts.append(f"## 调查员状态\n{investigators_state}")
        parts.append("")

    parts.append("请按系统提示词的格式，生成开场叙事 + 三个赌注式选项。")

    return "\n".join(parts)


def build_combat_resolution_prompt(
    encounter,
    *,
    chosen_option: str,
    round_number: int,
    action_description: str,
    investigators_state: str,
) -> str:
    """构造战斗结算的 user 消息 —— LLM 判定选项结果。

    参数:
        encounter: CombatEncounter 实例
        chosen_option: 被选中的选项文本（选项A/B/C的完整内容）
        round_number: 当前回合数
        action_description: 玩家执行的具体行动描述
        investigators_state: 调查员当前状态

    返回:
        user 消息文本
    """
    parts = [f"—— 第 {round_number} 回合结算 ——", ""]

    parts.append("## 被选择的行动")
    parts.append(chosen_option)
    parts.append("")

    if action_description:
        parts.append(f"## 玩家补充描述\n{action_description}")
        parts.append("")

    parts.append(f"## 当前敌人状态")
    alive = [e for e in encounter.enemies if e.is_alive()]
    if alive:
        for e in alive:
            parts.append(f"- {e.name} HP:{e.hp}/{e.hp_max}")
    parts.append("")

    if investigators_state:
        parts.append(f"## 调查员状态\n{investigators_state}")
        parts.append("")

    parts.append("## 你的任务：叙述行动结果")
    parts.append("")
    parts.append("1. 判定行动是否成功——根据选项中的检定要求进行掷骰判定（用 `<!--GS dice-->` 标记）")
    parts.append("2. 叙述成功的画面或失败的后果——要具体、有冲击力")
    parts.append("3. 结算所有代价——HP 变动、SAN 损失、状态效果")
    parts.append("4. 更新敌人状态——如果造成了伤害，明确写出剩余 HP")
    parts.append("5. 如果触发了胜负条件，在结尾声明")
    parts.append("")
    parts.append("用 `<!--GS ...-->` 标记所有状态变更。")

    return "\n".join(parts)


def build_combat_outcome_summary(outcome) -> str:
    """生成战斗结局的叙事摘要。

    参数:
        outcome: CombatOutcome 实例

    返回:
        叙事摘要文本
    """
    parts = ["—— 战斗结束 ——", ""]

    label_map = {
        "victory": "✧ 胜利",
        "defeat": "☠ 全军覆没",
        "flee": "⇡ 逃脱",
    }
    label = label_map.get(outcome.id, outcome.id)
    parts.append(f"**{label}**")

    if outcome.consequence:
        parts.append(f"\n{outcome.consequence}")
    if outcome.reward:
        parts.append(f"\n收获：{outcome.reward}")
    if outcome.provides_clues:
        parts.append(f"\n获得线索：{', '.join(outcome.provides_clues)}")

    return "\n".join(parts)


def build_round_escalation(encounter, round_number: int) -> str:
    """根据遭遇的 escalation 数据和当前回合数，生成升级叙事文本。

    参数:
        encounter: CombatEncounter 实例
        round_number: 当前回合数

    返回:
        升级叙事文本（可能为空字符串）
    """
    parts = []

    # 第1轮没有升级，是开场
    if round_number <= 1:
        return ""

    # 从 escalation 数组中取对应轮次的升级描述
    idx = round_number - 2  # escalation[0] 对应第2轮
    if encounter.escalation and idx < len(encounter.escalation):
        parts.append(encounter.escalation[idx])

    # 同时检查特殊规则中有没有时间相关内容（作为补充）
    for rule in encounter.special_rules:
        if "回合" in rule or "每轮" in rule or "持续" in rule:
            parts.append(f"⚠️ {rule}")

    return "\n".join(parts) if parts else ""


def build_narration_prompt(
    encounter,
    *,
    chosen_option: str,
    round_number: int,
    investigators_state: str,
    mech_result,
) -> str:
    """构造叙事润色提示词 —— LLM 收到已确定的机制结果后，负责叙述画面。

    与 build_combat_resolution_prompt 不同：此函数产出的是"请根据以下已知结果润色"
    而非"请判定这个行动的结果"。代码已经掷过骰、扣过血、查过结局，
    LLM 只需要把冰冷的数据变成有冲击力的叙事。

    参数:
        encounter: CombatEncounter 实例
        chosen_option: 被选中的选项完整文本
        round_number: 当前回合数
        investigators_state: 调查员状态文本
        mech_result: CombatMechanicResult —— 代码结算的完整结果
    """
    parts = [f"—— 第 {round_number} 回合结算 ——", ""]

    parts.append("## 被选择的行动")
    parts.append(chosen_option)
    parts.append("")

    parts.append("## 掷骰结果（已由系统判定，你不需要再判定）")
    if mech_result.test_rolled and mech_result.test_result:
        t = mech_result.test_result
        skill_name = mech_result.skill or "检定"
        result_word = "✅ 成功" if t.success else "❌ 失败"
        parts.append(f"{skill_name}检定：掷出 **{t.roll}**（目标 ≤{t.target}）→ {result_word}")
        if t.critical:
            parts.append("⚡ 大成功！")
        elif t.fumble:
            parts.append("💀 大失败！")
    elif mech_result.test_rolled:
        parts.append(f"{mech_result.skill or '检定'}：{mech_result.success and '✅ 成功' or '❌ 失败'}")
    elif mech_result.success:
        parts.append("自动成功（无需检定）")
    else:
        parts.append("自动失败（无需检定）")
    parts.append("")

    parts.append("## 伤害结算")
    if mech_result.damage_to_enemies:
        for eid, dmg in mech_result.damage_to_enemies.items():
            enemy = next((e for e in encounter.enemies if e.id == eid), None)
            name = enemy.name if enemy else eid
            remaining = f"（剩余 HP:{enemy.hp}/{enemy.hp_max}）" if enemy else ""
            parts.append(f"- 对 **{name}** 造成 {dmg} 点伤害{remaining}")
    else:
        parts.append("- 未对敌人造成伤害")

    if mech_result.damage_to_investigators:
        parts.append(f"- 调查员承受 {mech_result.damage_to_investigators} 点伤害")
    if mech_result.san_loss:
        parts.append(f"- 理智损失：{mech_result.san_loss} 点")
    parts.append("")

    parts.append("## 当前战场状态")
    alive = [e for e in encounter.enemies if e.is_alive()]
    if alive:
        for e in alive:
            parts.append(f"- {e.name} HP:{e.hp}/{e.hp_max}")
    else:
        parts.append("- 所有敌人已被击倒 ✧")
    parts.append("")

    if investigators_state:
        parts.append(f"## 调查员状态\n{investigators_state}")
        parts.append("")

    # 结局提示
    if mech_result.outcome:
        outcome = mech_result.outcome
        parts.append(f"## ⚡ 战斗结局已触发：{outcome.label or outcome.id}")
        if outcome.consequence:
            parts.append(outcome.consequence)
        parts.append("请在叙事结尾自然收束，为这场战斗画上句号。")
    else:
        parts.append("## 战斗继续")
        parts.append("请叙述本回合的画面——要具体、有冲击力。不需要判定胜负，结果已经确定。")

    return "\n".join(parts)
