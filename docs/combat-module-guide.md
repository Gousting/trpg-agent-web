# 战斗模块专题

弹幕直播 TRPG 的战斗系统设计文档。覆盖战斗循环、选项生成、结算流程和模块数据规范。

## 设计理念

弹幕观众不是单人 RPG 玩家——他们在围观 + 投票，没有时间也没有动机打字描述战术。核心体验是**集体戏剧**而非深度战术：

- **赌注式选项**：不是"攻击/利用环境/特殊行动"菜单，而是"你可以 X，但会付出 Y"的赌注。观众选的是"我愿意承受什么"，不是"我想做什么"。
- **逐轮升级**：环境随时间恶化，制造时间压力和集体紧张感。观众不是在打怪，是在看着局势越来越糟，每一票都是和时间赛跑。
- **量化代价**：绝不写"可能受伤"，写"DEX 检定（困难），失败则 1d6 坠落伤害"。

## 架构

```
combat/
  __init__.py      # 公共导出
  encounter.py     # 数据模型：Enemy / CombatEnvironment / CombatOutcome / CombatEncounter
  prompts.py       # LLM Prompt 构建：系统提示词 / 回合提示词 / 结算提示词
  loop.py          # 战斗调度器：CombatLoop 状态机
```

### 数据模型

```python
# 敌人
Enemy(id, name, hp, hp_max, armor, attack_bonus, damage, abilities, behavior, count)

# 环境
CombatEnvironment(terrain, hazards, lighting)

# 战斗结局
CombatOutcome(id, label, condition, provides_clues, next_location_type, consequence, reward)

# 完整遭遇
CombatEncounter(
    id, title, difficulty, description,
    enemies, environment, special_rules, escalation,
    outcomes, scaling, image, image_prompt
)
```

`Enemy` 有两个关键方法：

```python
enemy.is_alive()     # hp > 0
enemy.take_damage(n) # 返回实际扣血量（扣减护甲后）
```

`CombatEncounter` 的运行时方法：

```python
encounter.start()                            # 开始战斗（重置轮数 + active）
encounter.apply_scaling(player_count)        # 根据玩家数缩放敌人
encounter.check_outcome(fled=False, down=False) # 检查结局条件 → 返回结局 ID 或 ""
encounter.all_enemies_dead()                 # 所有敌人 hp <= 0
encounter.living_enemies()                   # 存活敌人列表
```

### 状态机

```
                    LLM 生成开场叙事
                    + 三个赌注式选项
         ┌──────────────┐
         │    ENTER     │──────────────┐
         └──────────────┘              │
                │                      │
                ▼                      │
         ┌──────────────┐              │
         │   VOTING     │  观众投票    │
         │  (等待投票)   │─────────────│
         └──────────────┘              │
                │                      │
                ▼                      │
         ┌──────────────┐              │
         │   RESOLVE    │  LLM 结算    │
         │   (判定结果)   │  叙述结果    │
         └──────────────┘              │
                │                      │
                ▼                      │
         ┌──────────────┐              │
         │CHECK_OUTCOME │              │
         │  (检查结局)   │              │
         └──────────────┘              │
           │            │              │
     未达结局       达结局             │
           │            │              │
           ▼            ▼              │
    回到 ENTER       END               │
    (下一轮)     (战斗结束)             │
                                       │
           战斗继续 ←──────────────────┘
```

## CombatLoop API

```python
from trpg_agent.combat import CombatLoop, CombatEncounter

encounter = CombatEncounter.from_dict(module_data)
loop = CombatLoop(encounter, investigators_state="调查员状态文本")

# ── 第一轮：生成选项 ──
system_prompt = loop.build_enter_prompt()   # 系统提示词（敌人面板/环境/规则/选项铁律）
user_prompt = loop.build_enter_user_prompt() # 用户消息（回合历史 + 当前状态 + 升级叙事）
# → 发送给 LLM，获取响应
llm_output = llm.chat(system_prompt, user_prompt)

# 解析 LLM 输出为结构化选项
round_state = loop.start_round(llm_output)
print(round_state.opening_narration)  # 开场叙事
for opt in round_state.options:       # 三个赌注式选项
    print(f"[{opt.option_key}] {opt.label}")

# ── 展示投票 ──
vote_text = loop.get_vote_format()  # 格式化的投票文案（叙事 + [A]/[B]/[C] 选项）

# ── 提交投票 ──
chosen = loop.submit_vote("A")  # 返回被选中的 CombatOption，无效 key 返回 None

# ── 结算本轮 ──
res_sys = loop.build_resolve_prompt()    # 复用战斗系统提示词（同 build_enter_prompt）
res_usr = loop.build_resolve_user_prompt() # 结算用户消息（被选行动 + 当前状态 + 结算指令）
resolution = llm.chat(res_sys, res_usr)
outcome = loop.resolve(resolution)

if outcome:  # 战斗结束 → 返回 CombatOutcome 对象
    print(loop.end_summary())
    print(f"结局 ID: {outcome.id}")  # victory / defeat / flee
else:  # 战斗继续 → 回到 build_enter_prompt() 开始下一轮
    pass
```

### 状态查询

```python
loop.is_active       # 战斗是否进行中（encounter.active 且 phase != "end"）
loop.is_ended        # 战斗是否已结束（phase == "end"）
loop.current_round   # 当前回合数
loop.current_phase   # enter / voting / resolve / end
loop.current_options # 当前回合的选项列表 [CombatOption, ...]
loop.last_narration  # 最近的叙事文本（结算叙事优先，回退到开场叙事）
loop.round_history   # 回合摘要列表 ["第1轮：摧毁祭坛→长老攻击-2", ...]
loop.outcome         # 战斗结局 CombatOutcome 或 None
```

## Prompt 设计

### 系统提示词 (`build_combat_system_prompt`)

告诉 LLM 它的角色和规则。ENTER 和 RESOLVE 阶段均调用此函数。

结构：
1. **敌人面板**：名称、HP、护甲、技能、行为模式
2. **环境**：地形、光照（含规则影响）、可互动要素
3. **特殊规则**：战斗专属机制（如洞穴回声触发钟乳石坠落）
4. **局势升级**：当前的 escalation 文本（仅 ENTER 阶段由 build_enter_user_prompt 注入）
5. **选项铁律**：
   - 每条必须有量化代价
   - 三条必须是真正的取舍
   - 覆盖不同维度（进攻/防守/环境/牺牲）
   - 利用场景专属要素
   - 输出格式：叙事 + `---` + 选项 A/B/C

### 回合提示词 (`build_combat_turn_user_prompt`)

提供当前战场状态。每次 ENTER 阶段调用。

结构：
1. 当前回合数
2. 战斗历史回顾（前几轮的摘要）
3. 局势变化（escalation 文本）
4. 当前敌人状态（存活敌人的 HP）
5. 调查员状态

### 结算提示词 (`build_combat_resolution_prompt`)

判定被选中的选项的结果。每次 RESOLVE 阶段调用。

结构：
1. 被选中的行动（完整选项文本）
2. 玩家补充描述（可为空）
3. 当前敌人状态
4. 调查员状态
5. 任务指令：判定成功/失败（掷骰用 `<!--GS dice-->` 标记）、叙述画面、结算代价、更新状态、声明胜负

### 选项解析 (`CombatLoop.parse_options`)

从 LLM 输出中提取开场叙事和三个选项。

规则：
- 按 `---` 分割 LLM 输出
- 第一部分 = 开场叙事
- 后续三个部分 = 选项 A/B/C
- 每个选项的第一行（`**粗体**` 文本）= 选项标签

## escalation 机制

`escalation` 是模块 JSON 中的一个字符串数组，定义逐轮升级叙事：

```json
"escalation": [
  "长老从黑水中完全浮出——蛙嘴张开发出第一声咆哮。全体 CON 检定，失败震慑 1 回合。",
  "洞穴震颤。钟乳石松动——枪声 30% 概率触发坠落（1d6）。",
  "混血深潜者一死一活——活着的进入狂暴（攻击+2，护甲-1）。",
  "黑水池暴涨！长老完全浸入水中——+2 护甲，再生翻倍。"
]
```

实现细节（`build_round_escalation` 函数）：
- `escalation[0]` → 第 2 轮触发
- `escalation[1]` → 第 3 轮触发
- 第 1 轮无升级（`round_number <= 1` 直接返回空字符串）
- 索引 `round_number - 2` 超出数组后无新升级
- 同时提取 `special_rules` 中含"回合/每轮/持续"关键词的规则作为补充

## 选项质量要求

### 合格示例

```
**摧毁古老祭坛**
攻击祭坛削弱长老。代价：浮雕内容让全体 SAN-1，
长老优先攻击破坏者。成功条件：STR 检定（普通），
成功后长老攻击 -2，持续 1d3 回合。
```

要素齐备：✅ 量化代价（全体 SAN-1）✅ 明确风险（被长老集火）✅ 检定条件（STR 普通）✅ 成功收益（攻击-2 1d3回合）✅ 利用场景要素（祭坛）

### 不合格示例

```
**攻击长老**
集中火力攻击长老。
```

缺失：❌ 无代价 ❌ 无风险 ❌ 无检定条件 ❌ 无收益预览 ❌ 与场景无关

## 新增战斗模块检查清单

- [ ] `module_type: "combat"`
- [ ] `enemies` 至少 1 个，每个有完整的 `hp`、`armor`、`damage`、`abilities`、`behavior`
- [ ] `environment` 有 `terrain` 描述和至少 2 个 `hazards`
- [ ] `special_rules` 至少 2 条战斗专属规则（JSON 中 key 可为 `rules` 或 `special_rules`）
- [ ] `escalation` 至少 2-4 条，内容与 `hazards` 和 `special_rules` 呼应
- [ ] `outcomes` 包含 `victory`、`defeat`、`flee` 三个结局，每个有 `consequence`
- [ ] `outcomes` 每个出口的 `provides_clues` 有其它模块的 `entry.required_clues` 可以接住
- [ ] `entry.location_types` 合理，与前置模块出口的 `next_location_type` 有交集
- [ ] `exits` 对应三个结局出口，每个产出不同线索
- [ ] `difficulty` 值合理（1-4）
- [ ] 可选：`image` / `image_prompt` 提供战斗背景图
- [ ] 验证：`python -m pytest tests/ -q` 全绿
