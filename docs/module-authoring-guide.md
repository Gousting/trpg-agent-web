# 模块开发指南

`data/modules/` 下每个子目录是一个模块。`ModuleComposer` 自动发现并组合它们为完整冒险。写模块只需填 `module.json`，引擎代码零改动。

## 模块类型总览（110个）

| 类型 | 数量 | `module_type` | 引擎行为 |
|---|---|---|---|
| 剧情 | 60 | `story` | `scenes` 场景序列 |
| 战斗 | 15 | `combat` | `encounter` 遭遇数据，桥接为战斗场景 + CombatLoop |
| 调查 | 10 | `investigation` | 同 story（语义标签，留待扩展） |
| 探索 | 8 | `exploration` | 同 story（语义标签，留待扩展） |
| 社交 | 8 | `social` | 同 story（语义标签，留待扩展） |
| 恐怖 | 5 | `horror` | 同 story（语义标签，留待扩展） |
| 休息 | 4 | `rest` | 同 story（语义标签，留待扩展） |

**重要**：组合引擎（`ModuleComposer`）层面只看 `module_type == "combat"` 这一个分支。其余六种类型（story/investigation/exploration/social/horror/rest）在组合引擎层面行为完全一致——都走场景序列流程。`module_type` 会作为 `from_type`/`to_type` 写入过渡场景的 `transition` 元数据，`web_server.py` 在生成跨模块过渡叙事指令时会读取它（映射为“战斗/调查/剧情”等中文标签，未在映射表中的类型回退为原始英文），提示 KP 在类型切换时（如调查→战斗）体现氛围升级；除此之外，这些标签目前仍主要用于模块组织和未来扩展，没有驱动其它引擎分支逻辑。

## 目录结构

```
data/modules/<module_id>/
  module.json          # 必需
  <scene_id>.png       # 可选，场景配图（引擎通过 _resolve_module_image 自动发现）
  combat.png           # 战斗模块配图（ComfyUI 生成）
```

## module.json 基础字段

所有模块类型共享的字段：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 模块唯一 ID，建议与目录名一致 |
| `title` | string | ✓ | 模块标题，用于展示和过渡文案 |
| `module_type` | string | - | `story`/`combat`/`investigation`/`exploration`/`social`/`horror`/`rest`，默认 `story`。组合引擎仅 `combat` 触发不同场景生成行为，其余为语义标签；`web_server.py` 会消费该字段生成过渡叙事的中文类型标签 |
| `genre` | string[] | - | 题材标签，如 `["horror","mythos","underwater"]` |
| `difficulty` | int | - | 难度 1-4，配合 `--difficulty-range` 过滤 |
| `duration_estimate` | string | - | 展示用，如 `"2-3 turns"` |
| `is_ending` | bool | - | 结局模块打 `true`，BFS 到此后停止扩展 |
| `entry` | object | - | 入场条件，见下文 |
| `exits` | object[] | - | 多出口数组，见下文 |
| `scenes` | object[] | 非 combat✓ | 场景卡数组（combat 类型不需要） |
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

1. **随机兼容匹配**：引擎根据 `exits` 的 `provides_clues` + `next_location_type` 在模块池中找 `entry` 匹配的模块。写模块时填对字段即可，引擎自动处理。
2. **手写显式跳转**：在模块最后一个场景的 `exit_labels` 里写跨模块跳转，key 格式 `"目标模块id::目标场景原始id"`。不检查 `entry` 条件，保证可达。

---

## 各类型模块详解

### 剧情模块（`story`）—— 最常用

`scenes` 数组定义场景序列，场景之间通过 `leads_to` 形成内部流转，跨模块跳转通过 `exit_labels` 声明。

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

**入口方法**：`ModuleComposer.compose()` → BFS 遍历模块图 → `_prefix_scenes()` 给场景 ID 加模块前缀 → 根据 `leads_to` 连接内部场景 → 根据 `exit_labels`/`exits` 拼接跨模块过渡。

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

无 `scenes` 数组。用完整的遭遇数据定义敌人、环境、特殊规则和结局。引擎自动将遭遇数据桥接为战斗场景 + 结局过渡场景，运行时通过 `CombatLoop` 驱动回合制战斗。

```json
{
  "id": "deep_one_lair",
  "module_type": "combat",
  "title": "深潜者巢穴",
  "difficulty": 3,
  "description": "潮水退去后，海蚀洞深处露出一道非自然的石门...",
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
  "special_rules": [
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

**JSON 字段到 Python 属性的映射**：`enemies` → `Enemy` 对象列表、`environment` → `CombatEnvironment`、`special_rules`（JSON key 也可写作 `rules`，两者兼容）→ Python 属性 `special_rules`、`outcomes` → `dict[str, CombatOutcome]`。

**引擎桥接**：`ModuleComposer._assemble()` 检测 `module_type == "combat"` 后调用 `_combat_to_scene()` 将遭遇数据转为 `Scene` 对象（`combat.enabled=True`，`combat.encounter` 持有完整数据），并为每个结局出口生成过渡场景。web_server 看到 `combat.enabled` 后切换战斗模式 prompt 并启动 `CombatLoop`。

**详细 API**：见 `docs/combat-module-guide.md`。

### 调查模块（`investigation`）

语义标签，引擎行为同 story。用于标记以线索收集和推理为核心的模块。`exits` 通常产出多个关键线索。

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

### 探索模块（`exploration`）

语义标签，引擎行为同 story。用于标记以地点发现和地形穿越为核心的模块。

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

语义标签，引擎行为同 story。用于标记以 NPC 对话和信息收集为核心的模块。`scenes` 中的 `opportunities` 和 `npcs_here` 为关键场景级字段。

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

语义标签，引擎行为同 story。用于标记以心理冲击和 SAN 检定为核心的模块。`scenes` 中的 `san_check` 和 `mood` 为关键场景级字段。

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

语义标签，引擎行为同 story。用于标记以恢复和整理线索为核心的喘息节点。通常没有 `required_clues`，出现在高强度模块之后。

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
| `image` | 场景图的相对 URL（引擎通过 `_resolve_module_image` 自动发现，无需手写） |
| `mood` | 场景氛围，驱动 BGM 选择 |
| `san_check` | COC SAN 检定 `{"trigger":"...","level":"MAJOR/MINOR"}` |
| `combat` | 剧情模块内嵌套的单场景战斗触发器 `{"enabled": true, "encounter": ...}`（与 `combat` 类型模块不同，此为场景内嵌战斗） |
| `npcs_here` | 场景出场 NPC 名称列表 `["老水手", "旅馆老板"]` |

---

## 叙事语言约束（写场景/NPC 文案前必读）

KP 的实际叙述由 LLM 实时生成，但模块里的**静态文案**（`title`、`description`、`opportunities[].text`、NPC 台词、`vote_prompt`、`image_prompt`）是它的素材——素材带 AI 味，叙述就会带 AI 味。写模块时遵守以下规则：

**1. 语言基调按世界观（对应 `prompts/tone_{world}_zh.md`）**

| 世界观 | 基调文件 | 一句话主基调 |
|---|---|---|
| `coc` | `prompts/tone_coc_zh.md` | 克制文学、短句留白、听觉气味优先、日常物件比喻、不解释不露全貌 |
| `harry_potter` | `prompts/tone_harry_potter_zh.md` | 魔幻轻快、正经的荒诞、魔法藏在细节里（东西不听话）、对话有机锋 |
| `infinite_flow` | `prompts/tone_infinite_flow_zh.md` | 网文快节奏、动词有力、主神空间【系统提示】机械腔与副本生存压迫感严格区分 |

写作时按对应基调的 few-shot 标杆模仿腔调，不要用通用小说腔糊弄。

**2. 通用禁令（对所有世界观生效，源自 kp_core_zh.md「语言卫生」）**

- 禁止排比三连：`黑暗、潮湿、寂静……` 这种顿号堆砌意象。两个并列是极限。
- 禁止升华总结：不以"这一刻""仿佛整个世界""时间仿佛凝固了"开场或收尾。
- 禁用词：值得注意的是、与此同时、换句话说、总而言之、综上所述、某种意义上、在某种程度上、仿佛在诉说着、仿佛整个世界。
- 禁止流水线描写：`嘴角勾起一抹冷笑`、`眼神一凛`、`心中一惊`、`倒吸一口凉气`。
- 禁止同段重复用词：同一形容词（诡异、神秘、阴森）不要在一段里出现两次。
- 禁止解释性旁白：不写"这意味着……""这说明……"，让行为自己说话。

**3. 文案要具体，不要形容词堆叠**

- `description` 写给 LLM 看，但它会被二次叙述——写得越具体（一个感官细节、一个反常物件），LLM 输出的质感越好。
- ❌ `黑暗的走廊里弥漫着诡异的气氛`
- ✅ `走廊尽头的水龙头没关紧，滴水声每隔七秒响一次，比钟还准`

**4. NPC 台词要像人话**

- 台词口语化，带个性（爱抱怨的、傲慢的、热心过头的）。NPC 不是信息公告栏。
- ❌ `"请告诉我你们来此的目的，我或许能提供帮助。"`
- ✅ `"又是来找失踪课本的？柜子会咬人，别怪我没提醒。"`

**5. `image_prompt` 走视觉基调（对应 `scene_tags.json` / Z-Image 风格）**

- 与文案同源：写实恐怖、暗色调、具体场景物件，不要抽象概念词。
- 参考现有模块的 `image_prompt` 写法保持一致。

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
