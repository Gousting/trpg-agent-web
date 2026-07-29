# 新建剧情模块注意事项

给 `data/modules/` 下新增模块（`module.json`）时的实用指南。目标：写完就能被
`ModuleComposer` 自动发现、正确挂进分支图，不会变成"孤儿模块"。

## 1. 目录结构

```
data/modules/<module_id>/
  module.json          # 必需
  <scene_original_id>.png / .jpg   # 可选，场景配图（按原始场景 id 命名，不带模块前缀）
```

`ModuleComposer.load_all()` 会扫描 `data/modules/` 下**每一个包含 `module.json` 的子目录**，
纯目录发现，不需要在任何地方注册模块 id——**引擎代码不用改**。

## 2. `module.json` 顶层字段

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | 是 | 模块 id，建议和目录名一致 |
| `title` | 是 | 模块标题 |
| `genre` | 否 | 题材标签数组，目前仅作展示/筛选用 |
| `difficulty` | 否 | 整数难度，配合 `--difficulty-range` 之类的范围过滤 |
| `duration_estimate` | 否 | 展示用文案，例如 `"2-3 turns"` |
| `is_ending` | 否 | **结局模块打 `true`**（见第 5 节），默认 `false` |
| `entry` | 否 | 入场条件，见第 3 节 |
| `exits` | 否 | 多出口数组，见第 4 节；也支持旧的单出口 `exit: {...}` |
| `scenes` | 是 | 场景卡数组，见第 6 节 |
| `npcs` | 否 | NPC 战斗数据数组 |
| `variance` | 否 | Roguelike 随机变体配置（线索/NPC/氛围），见 `adventure/variance.py` |

## 3. 入场条件 `entry`

```json
"entry": {
  "location_types": ["indoor", "basement"],
  "required_clues": ["cursed_slab_found"],
  "forbidden_clues": ["keepers_trust"],
  "mood": null
}
```

- `location_types` 留空表示"任何地点都能进"，非空则需要和上游出口的 `next_location_type`
  有交集才能被**随机匹配**选中。
- `required_clues` 留空 = 该模块会自动进入"合法开局候选池"（`_pick_start` 只挑
  `required_clues` 为空的模块）。**如果不想让它被随机抽成开场，必须给它一个线索门槛**——
  目前没有 `entry_only` / `no_start` 这类开关。
- `required_clues` 里写的每个线索名，必须是**其它模块 `exits[].provides_clues` 里
  真实产出过的字符串**，否则这个模块永远无法通过"随机兼容匹配"路径进入（除非有模块显式
  手写链接到它，见第 4 节，手写链接不检查 `entry`）。

## 4. 出口 `exits` 与两条互相叠加的分支路径

```json
"exits": [
  {"id": "to_a", "label": "去 A", "provides_clues": ["clue_x"], "mood": "dread", "next_location_type": "indoor"},
  {"id": "to_b", "label": "去 B", "provides_clues": ["clue_x", "clue_y"], "next_location_type": "outdoor",
   "requires": {"resolved_element": "some_scene_element_id"}}
]
```

模块之间有两条**会叠加、互不排斥**的连接方式（2026-07-29 之前是互斥的，已修复）：

1. **随机兼容匹配**：组合引擎根据 `provides_clues` + `next_location_type` 自动在模块池里找
   `entry` 条件匹配的模块，随机接上。写模块时通常不用管这条——只要 `entry`/`exits` 字段填对，
   引擎自动处理。
2. **手写显式跳转**：在模块**最后一个场景**（`scenes` 数组最后一项）里写 `exit_labels` /
   `exit_requires`，key 用 `"目标模块id::目标模块内场景原始id"` 格式：

   ```json
   "exit_labels": {
     "basement_confrontation::basement": "去老宅地下室——仪式的主场地",
     "escape_chase::woods": "情况不对——快跑"
   }
   ```

   手写跳转**不检查目标模块的 `entry` 条件**（既不查线索也不查地点类型），只要目标模块 id
   存在就一定能连上。适合"剧情上必须走这条线"的关键节点，或者用来**兜底**——如果你的新
   模块地点类型/线索和现有内容对不上、随机匹配帮不上忙，直接在某个上游模块的最后场景加一条
   手写 `exit_labels` 指向它，就一定可达。

> 两条路径现在是**叠加**的：一个模块只要有手写目标，随机匹配仍然会在同一个出口上追加其它
> 兼容候选，不会互相屏蔽。

## 5. `is_ending`：结局模块

```json
{"id": "basement_confrontation", "is_ending": true, ...}
```

如果新模块是"胜利/逃脱/死亡"之类的收尾节点，**一定要打 `is_ending: true`**。否则组合引擎
在 `max_depth` 用完之前，仍可能把这个"结局"之后随机接上别的调查模块，产生"打完最终战又莫名
其妙回去查案"的割裂感。打了 `is_ending` 之后，BFS 到这个节点直接停止扩展，不再生成任何出边。

## 6. 场景卡 `scenes[]` 关键字段

| 字段 | 说明 |
|---|---|
| `id` | 模块内场景 id（不带模块前缀，组合器会自动加 `<module_id>::` 前缀） |
| `leads_to` | 模块**内部**跳转目标（同模块场景 id，不带 `::`） |
| `exit_labels` / `exit_requires` | 只在**最后一个场景**里写才会被当成"模块间"手写跳转 |
| `image_prompt` | 用于生图的提示词 |
| `vote_prompt` | 非空则该场景触发弹幕投票 |
| `san_check` / `combat` | COC 专用触发器，格式见 `Scene` 类文档字符串 |

## 7. 新增/修改模块后必须做的验证

1. 跑一遍全量测试：

   ```powershell
   python -m pytest -q
   ```

   如果 `test_difficulty_filter` 之类"固定种子 + 断言选中某个特定模块"的用例挂了，**通常不是
   bug**——新模块进入了同一批候选池，改变了 `random.Random(seed).choice()` 的结果。视情况更新
   断言，或把用例改成 `start_module=` 强制起点。

2. 跑一遍可达性抽样，确认新模块真的会出现在自然流程里（不强制起点）：

   ```powershell
   python -c "
   import sys; sys.path.insert(0, '.')
   from trpg_agent.adventure.module_composer import ModuleComposer
   from pathlib import Path
   c = ModuleComposer(Path('data/modules'))
   c.load_all()
   seen = set()
   for seed in range(500):
       b = c.compile(seed=seed, max_depth=8)
       seen.update(b.module_ids)
   all_ids = set(c._modules.keys())
   print('从未出现的模块:', sorted(all_ids - seen))
   "
   ```

   如果新模块出现在"从未出现"里，回到第 3、4 节检查 `entry`/`exits` 是否和现有线索词汇/地点
   类型对得上，或者补一条手写 `exit_labels`。

## 8. 新建模块检查清单

- [ ] `entry.required_clues` 里的每个线索名，都有其它模块的 `exits[].provides_clues` 产出过
      （或者已经给它加了手写跳转来源）
- [ ] `entry.location_types` 和某个产出对应线索的出口的 `next_location_type` 有交集（或已有
      手写跳转兜底）
- [ ] 如果是结局模块：`is_ending: true`
- [ ] 如果不想被随机抽为开场：`entry.required_clues` 非空
- [ ] 500 种子可达性抽样里出现过
- [ ] `python -m pytest -q` 全绿
