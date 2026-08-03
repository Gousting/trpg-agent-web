# 无限流多副本模式——后续开发计划

## 目标

在现有模块组合引擎上支持「无限流」世界观：主神空间（hub）作为枢纽，玩家进入独立副本（dungeon）冒险，通关后返回主神空间选择下一个副本。核心诉求：**副本模块命中不冲突**——不同副本的模块互不串线，副本可被多次进入，且不破坏现有 COC / 哈利波特模块。

## 已完成（本批次）

### 1. `reusable` 模块标记

位置：`trpg_agent/adventure/module_composer.py`

- `ModuleMeta` 新增 `reusable: bool = False` 字段，`from_dict` 解析 JSON 中同名键
- 新增 `_usable(mod, used_ids)` helper，`_find_compatible` 三处 `used_ids` 过滤全部改走它
- 语义：普通模块（默认 False）一局内只出现一次；reusable 模块（True）豁免去重，可被多次进入
- **零影响**：现有 COC / 哈利波特模块不声明该字段，走原逻辑；BFS 深度上限 + node_map 复用仍是防死循环双保险

验证：`tests/test_reusable_modules.py` 6 个用例全过，全量 186 个测试无回归。

## 后续计划

### 2. 副本模块数据设计（纯数据层，零代码改动）

**隔离机制：`location_type` 唯一化**

每个副本分配唯一地点类型，模块匹配靠 `entry.location_types` 与上游出口 `next_location_type` 的交集，交集为空即不命中：

| 模块 | entry.location_types | 出口 next_location_type |
|---|---|---|
| hub（reusable） | `["hub"]` | `dungeon_rs` / `dungeon_jy` / `dungeon_xt` |
| 副本入口（生化） | `["hub"]` | `dungeon_rs` |
| 副本内部（生化） | `["dungeon_rs"]` | `dungeon_rs` |
| 副本通关（生化） | `["dungeon_rs"]` | `hub` |
| 副本入口（咒怨） | `["hub"]` | `dungeon_jy` |

副本 A 的模块与副本 B 的入口 location_type 无交集 → 永不串线。

**顺序控制：手写跳转锁死副本内部剧情**

副本内部场景 `exit_labels` 显式写 `"下一模块ID::场景ID"`，composer 的 `_authored_external_targets` 优先于随机匹配，副本剧情线由作者完全控制。

**文件布局**

```
data/modules_infinite_flow/
  hub_plaza/module.json          # reusable: true
  dungeon_resident_evil/...      # 生化危机副本（4-6 层）
  dungeon_juon/...               # 咒怨副本
  dungeon_xiuxian/...            # 修仙副本
```

### 3. 冲突规避清单

1. **副本串线**：`_find_compatible` 的渐进放宽会忽略 location_type——规避办法是副本内部模块数量足够、线索链完整，保证每个出口都能匹配到候选，永不触发放宽分支
2. **hub 多出口撞同一副本入口**：副本入口模块 entry 要求不同线索，让它们互斥
3. **深度限制**：`max_depth` 当前默认 3，副本链 = hub + 内部 N 层 + 结算，需调大到 4-6
4. **多副本循环**：已由 `reusable` 字段解决——hub 豁免去重可被多次进入，BFS 深度上限防死循环

### 4. 待办

- [ ] 编写 hub_plaza 模块（reusable: true，3+ 副本出口）
- [ ] 编写 3 个副本模块池（生化 / 咒怨 / 修仙），每个 4-6 层剧情链
- [ ] `web_server.py` 的 `world` 参数已支持多目录——接入 `data/modules_infinite_flow/`
- [ ] 验证：主神空间 → 进副本 A → 通关 → 返回 hub → 进副本 B 全链路
- [ ] 评估：轮回者三维属性（力量/敏捷/精神）+ 强化树是否进本阶段
