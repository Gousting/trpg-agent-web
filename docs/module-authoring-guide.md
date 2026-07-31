# 模块开发指南

`data/modules/` 下每个子目录是一个模块。`ModuleComposer` 自动发现并组合它们为完整冒险。
写模块只需填 `module.json`，引擎代码零改动。

## 模块类型总览（110个）

| 类型 | 数量 | `module_type` | 入口方式 |
|---|---|---|---|
| 剧情 | 60 | `story` | `scenes` 场景序列 |
| 战斗 | 15 | `combat` | `encounter` 遭遇数据（无 `scenes`） |
| 调查 | 10 | `investigation` | `scenes` + 线索产出 |
| 探索 | 8 | `exploration` | `scenes` + 地点发现 |
| 社交 | 8 | `social` | `scenes` + NPC 交互 |
| 恐怖 | 5 | `horror` | `scenes` + SAN 检定 |
| 休息 | 4 | `rest` | `scenes` + 恢复机制 |

所有类型共用相同的基础字段（`id`、`title`、`entry`、`exits`），差异在 `module_type` 触发的
引擎行为。

## 目录结构

```
data/modules/<module_id>/
  module.json          # 必需
  <scene_id>.png       # 可选，场景配图
  combat.png           # 战斗模块配图（ComfyUI 生成）
```

## module.json 基础字段

所有模块类型共享的字段：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 模块唯一 ID，建议与目录名一致 |
| `title` | string | ✓ | 模块标题，用于展示和过渡文案 |
| `module_type` | string | - | `story`/`combat`/`investigation`/`exploration`/`social`/`horror`/`rest`，默认 `story` |
| `genre` | string[] | - | 题材标签，如 `["horror","mythos","underwater"]` |
| `difficulty` | int | - | 难度 1-4，配合 `--difficulty-range` 过滤 |
| `duration_estimate` | string | - | 展示用，如 `"2-3 turns"` |
| `is_ending` | bool | - | 结局模块打 `true`，BFS 到此后停止扩展 |
| `entry` | object | - | 入场条件，见下文 |
| `exits` | object[] | - | 多出口数组，见下文 |
| `scenes` | object[] | 剧情类✓ | 场景卡数组（combat 类型不需要） |
| `npcs` | object[] | - | NPC 数据 |
| `variance` | object | - | Roguelike 随机变体 |

## 入场条件 `entry`

```json
"entry": {
  "location_types": ["indoor", "basement"],
  "required_clues": ["cursed_slab_found"],
  "forbidden_clues": [],
  "mood": "dread"
}
```

- `location_types`：该模块可以出现的场所类型。空数组 = 任何地点都能进。
- `required_clues`：进入该模块需要调查员持有的线索。**空数组 = 该模块可被随机选为开场起点**。
- `forbidden_clues`：如果队伍持有这些线索，该模块不可进入（做互斥分支用）。
- `mood`：入场氛围，驱动 BGM 选择和过渡叙事风格。

## 出口 `exits`

```json
"exits": [
  {
    "id": "to_a",
    "label": "前往地下室",
    "provides_clues": ["basement_ritual_found"],
    "mood": "dread",
    "next_location_type": "basement",
    "requires": {"resolved_element": "key_found"}
  }
]
```

两条并行分支路径，互不排斥会叠加：

1. **随机兼容匹配**：引擎根据 `exits` 的 `provides_clues` + `next_location_type` 在模块池
   中找 `entry` 匹配的模块。写模块时填对字段即可，引擎自动处理。
2. **手写显式跳转**：在模块最后一个场景的 `exit_labels` 里写跨模块跳转，
   key 格式 `"目标模块id::目标场景原始id"`。不检查 `entry` 条件，保证可达。

---

## 各类型模块入口方法

### 剧情模块（`story`）

最常见类型。`scenes` 数组定义场景序列，场景之间通过 `leads_to` 形成内部流转，
跨模块跳转通过 `exit_labels` 声明。

```json
{
  "id": "library_research",
  "module_type": "story",
  "title": "图书馆研究",
  "difficulty": 2,
  "entry": {
    "location_types": ["indoor", "institutional"],
    "required_clues": ["old_ledger_found"],
    "mood": "tension"
  },
  "scenes": [
    {
      "id": "library",
      "title": "图书馆阅览室",
      "description": "昏暗的老图书馆，书架上积满灰尘...",
      "leads_to": ["library_restricted"],
      "opportunities": [{"id": "l1", "text": "查阅地方报纸存档"}],
      "vote_prompt": "下一步调查方向？",
      "exit_labels": {
        "basement_confrontation::basement": "线索指向老宅地下室",
        "escape_chase::woods": "发现有人在跟踪你们——快走"
      }
    },
    {
      "id": "library_restricted",
      "title": "禁书区",
      "description": "...",
      "leads_to": ["library_converge"]
    },
    {
      "id": "library_converge",
      "title": "研究总结",
      "description": "将所有线索拼在一起..."
    }
  ],
  "exits": [
    {"id": "to_basement", "label": "去老宅", "provides_clues": ["ritual_location"], "next_location_type": "basement"},
    {"id": "to_escape", "label": "逃离", "provides_clues": ["stalked"], "next_location_type": "outdoor"}
  ]
}
```

**入口方法**：`ModuleComposer.compose()` → BFS 遍历模块图 → `_prefix_scenes()` 给场景 ID
加模块前缀 → 根据 `leads_to` 连接内部场景 → 根据 `exit_labels`/`exits` 拼接跨模块过渡。

核心 API：
```python
from trpg_agent.adventure.module_composer import ModuleComposer
from pathlib import Path

composer = ModuleComposer(Path("data/modules"))
composer.load_all()
adv, seed = composer.compose(seed=42, max_depth=4)
# adv 是标准 Adventure 对象，start_scene 指向起点
```

### 战斗模块（`combat`）

无 `scenes` 数组。用 `encounter` 定义敌人、环境、特殊规则和结局。
引擎自动将遭遇数据桥接为战斗场景 + 结局过渡场景。

```json
{
  "id": "deep_one_lair",
  "module_type": "combat",
  "title": "深潜者巢穴",
  "difficulty": 3,
  "entry": {
    "location_types": ["cave", "underground", "coastal"],
    "required_clues": ["coastal_cave_ritual_evidence"],
    "mood": "dread"
  },
  "enemies": [
    {
      "id": "deep_one_elder",
      "name": "深潜者长老",
      "hp": 18, "armor": 2,
      "attack_bonus": 2,
      "damage": "1d8+1",
      "abilities": [
        {"name": "蛙鸣咆哮", "effect": "锥形范围，CON 检定失败则震慑 1 回合。冷却 3 回合"},
        {"name": "水生再生", "effect": "站在水中时每回合恢复 3 HP"}
      ],
      "behavior": "坚守在巢穴中心的水池中，不退却，优先攻击远程调查员",
      "count": 1
    }
  ],
  "environment": {
    "terrain": "潮汐侵蚀出的海蚀洞穴，中央是一池黑色深水",
    "hazards": [
      "中央深水池——踏入水中移动速度减半，长老在水中可触发再生",
      "钟乳石——枪声或爆炸可能震落（区域内 1d6 伤害）"
    ],
    "lighting": "bioluminescent"
  },
  "rules": [
    "洞穴回声——枪声触发钟乳石坠落（20% 概率）",
    "长老在水中时获得 +1 护甲和再生能力——用火把或电击可逼它上岸"
  ],
  "escalation": [
    "长老从黑水中完全浮出——它的蛙嘴张开，发出第一声咆哮。所有调查员需 CON 检定。",
    "洞穴开始震颤。天花板上的钟乳石群开始松动——枪声有 30% 概率触发坠落。",
    "混血深潜者一死一活——活着的进入狂暴（攻击 +2，护甲 -1）。",
    "黑水池暴涨！长老完全浸入水中——+2 护甲，再生翻倍。再不解决就没有退路了。"
  ],
  "outcomes": {
    "victory": {
      "condition": "击倒所有深潜者",
      "provides_clues": ["deep_one_artifact", "ocean_ritual_knowledge"],
      "reward": "祭坛下发现古老三叉戟碎片和三块记录潮汐仪式的石板",
      "consequence": "洞窟恢复了令人窒息的寂静"
    },
    "defeat": {
      "condition": "全体调查员 HP=0 或落水",
      "provides_clues": ["sacrificed_to_dagon"],
      "consequence": "被深潜者拖入黑色水池"
    },
    "flee": {
      "condition": "任意调查员成功逃出洞穴",
      "provides_clues": ["lair_location_mapped"],
      "consequence": "冲出洞口的瞬间听到身后传来愤怒的蛙鸣"
    }
  }
}
```

**入口方法**：

```python
from trpg_agent.combat import CombatEncounter, CombatLoop

# 1. 从模块 JSON 加载遭遇数据
encounter = CombatEncounter.from_dict(module_data)

# 2. 创建战斗循环
loop = CombatLoop(encounter, investigators_state="调查员状态文本")

# 3. 第一轮：生成选项
sys_prompt = loop.build_enter_prompt()
user_prompt = loop.build_enter_user_prompt()
# → 发送给 LLM，获取响应
llm_output = llm.chat(sys_prompt, user_prompt)
round_state = loop.start_round(llm_output)

# 4. 展示选项给弹幕，收集投票
vote_format = loop.get_vote_format()  # 格式化的投票文案
# → 弹幕投票

# 5. 提交投票并结算
loop.submit_vote("A")
res_sys = loop.build_resolve_prompt()
res_usr = loop.build_resolve_user_prompt()
resolution = llm.chat(res_sys, res_usr)
outcome = loop.resolve(resolution)

# 6. 如果 outcome 为空 → 战斗继续，回到步骤 3
#    如果 outcome 非空 → 战斗结束
if outcome:
    print(loop.end_summary())
```

**CombatLoop 状态机**：

```
ENTER → [LLM 生成开场叙事 + 三选项] → VOTING → [提交投票]
  → RESOLVE → [LLM 结算行动] → CHECK_OUTCOME
    → 未达结局：回到 ENTER（下一轮）
    → 达结局：END（输出结局叙事）
```

**选项设计原则**：赌注式而非菜单式。每条选项必须量化代价（HP/SAN/检定难度），
三条选项之间必须有真正的取舍。详见 `combat/prompts.py`。

**escalation 数组**：逐轮升级叙事，`escalation[0]` 对应第 2 轮（第 1 轮无升级），
每轮自动注入系统提示词和 user prompt。环境随时间恶化，制造集体戏剧张力。

### 调查模块（`investigation`）

专注于线索收集和推理。`scenes` 通常包含多个可互动的调查元素，`exits` 产出关键线索。

```json
{
  "id": "code_breaking",
  "module_type": "investigation",
  "title": "密文破译",
  "difficulty": 3,
  "entry": {
    "location_types": ["indoor", "institutional"],
    "required_clues": ["summoning_ritual_details"],
    "mood": "tension"
  },
  "scenes": [
    {
      "id": "study",
      "title": "语言学研究室",
      "description": "...",
      "opportunities": [
        {"id": "c1", "text": "对比古代语言语法"},
        {"id": "c2", "text": "查找星位对应关系"}
      ],
      "vote_prompt": "优先分析哪个方向？"
    }
  ]
}
```

**入口方法**：同剧情模块，差异仅在 `module_type` 标签影响氛围过渡文案。

### 探索模块（`exploration`）

发现新地点、穿越危险地形。`entry.location_types` 通常与自然/野外/地下相关。

```json
{
  "id": "cliff_ruins",
  "module_type": "exploration",
  "title": "悬崖遗迹",
  "difficulty": 2,
  "entry": {
    "location_types": ["outdoor", "coastal"],
    "required_clues": ["low_tide_access"],
    "mood": "tension"
  }
}
```

### 社交模块（`social`）

NPC 对话、交易、信息收集。难度通常较低，`scenes` 中的 `npcs_here` 和对话选项为核心。

```json
{
  "id": "dockworker_tips",
  "module_type": "social",
  "title": "码头工人线人",
  "difficulty": 1,
  "entry": {
    "location_types": ["waterfront", "industrial"],
    "required_clues": ["warehouse_inventory"],
    "mood": "tension"
  },
  "scenes": [
    {
      "id": "docks",
      "title": "码头边",
      "npcs_here": ["老水手"],
      "opportunities": [
        {"id": "d1", "text": "递上一瓶威士忌"},
        {"id": "d2", "text": "出示警徽追问"}
      ]
    }
  ]
}
```

### 恐怖模块（`horror`）

SAN 检定、心理冲击、超自然遭遇。`scenes` 中通常包含 `san_check` 触发器。

```json
{
  "id": "ancestral_memory",
  "module_type": "horror",
  "title": "先祖记忆",
  "difficulty": 3,
  "entry": {
    "location_types": ["indoor"],
    "required_clues": ["artifact_origin_traced"],
    "mood": "tension"
  },
  "scenes": [
    {
      "id": "vision",
      "title": "共享噩梦",
      "san_check": {"trigger": "触碰古老器物", "level": "MAJOR"}
    }
  ]
}
```

### 休息模块（`rest`）

恢复 HP/SAN、整理线索的喘息节点。无 `required_clues`，通常出现在战斗或恐怖模块之后。

```json
{
  "id": "church_sanctuary",
  "module_type": "rest",
  "title": "教堂避难",
  "difficulty": 1,
  "entry": {
    "location_types": ["indoor"],
    "mood": "tension"
  }
}
```

---

## 场景卡 `scenes[]` 关键字段

| 字段 | 说明 |
|---|---|
| `id` | 模块内场景 ID（不带前缀，组合器自动加 `module_id::`） |
| `title` | 场景标题 |
| `description` | DM 看到的场景描述，LLM 的上下文 |
| `leads_to` | 模块内部跳转目标（同模块场景原始 id） |
| `opportunities` | 可互动元素 `[{"id":"x","text":"..."}]` |
| `exit_labels` | 跨模块跳转标签（只在最后一个场景写），key 为 `"目标模块id::场景id"` |
| `exit_requires` | 跨模块跳转的门控条件，`{"目标id": "required_element_id"}` |
| `vote_prompt` | 非空触发弹幕投票 |
| `image_prompt` | ComfyUI 生图提示词 |
| `mood` | 场景氛围，驱动 BGM 选择 |
| `san_check` | COC SAN 检定 `{"trigger":"...","level":"MAJOR/MINOR"}` |
| `combat` | 剧情模块内嵌套的单场景战斗触发器（与 `combat` 类型模块不同） |

---

## 验证清单

1. **全量测试**：
   ```bash
   python -m pytest tests/ -q
   ```

2. **可达性抽样**（500 种子确认新模块出现在自然流程中）：
   ```python
   from trpg_agent.adventure.module_composer import ModuleComposer
   from pathlib import Path
   c = ModuleComposer(Path("data/modules"))
   c.load_all()
   seen = set()
   for seed in range(500):
       b = c.compile(seed=seed, max_depth=8)
       seen.update(b.module_ids)
   missing = sorted(set(c._modules.keys()) - seen)
   print("从未出现:", missing)
   ```

3. **新建模块检查清单**：
   - [ ] `entry.required_clues` 的每个线索有其它模块产出（或有手写跳转来源）
   - [ ] `entry.location_types` 与来源出口的 `next_location_type` 有交集
   - [ ] 结局模块：`is_ending: true`
   - [ ] 不想被随机抽为开场：`entry.required_clues` 非空
   - [ ] 战斗模块：`enemies`、`environment`、`outcomes` 完整，`escalation` 至少 2-4 轮
   - [ ] 500 种子抽样出现过
   - [ ] `pytest -q` 全绿
