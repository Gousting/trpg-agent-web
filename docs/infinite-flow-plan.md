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
| 副本入口（生化） | `["dungeon_rs"]` | `dungeon_rs` |
| 副本内部（生化） | `["dungeon_rs"]` | `dungeon_rs` |
| 副本通关（生化） | `["dungeon_rs"]` | `hub` |
| 副本入口（咒怨） | `["dungeon_jy"]` | `dungeon_jy` |

副本 A 的模块与副本 B 的入口 location_type 无交集 → 永不串线。

**顺序控制：手写跳转锁死副本内部剧情**

副本内部场景 `exit_labels` 显式写 `"下一模块ID::场景ID"`，composer 的 `_authored_external_targets` 优先于随机匹配，副本剧情线由作者完全控制。

**文件布局**

```
data/modules_infinite_flow/
  hub_plaza/module.json          # reusable: true
  dungeon_rs_*/module.json       # 生化危机副本（4 模块链）
  dungeon_juon_*/module.json     # 咒怨副本（4 模块链）
  dungeon_xiuxian_*/module.json  # 修仙副本（4 模块链）
```

### 3. 冲突规避清单

1. **副本串线**：`_find_compatible` 的渐进放宽会忽略 location_type——规避办法是副本内部模块数量足够、线索链完整，保证每个出口都能匹配到候选，永不触发放宽分支
2. **hub 多出口撞同一副本入口**：副本入口 entry 要求不同线索（`forbidden_clues` 禁止其他副本的 `*_entered` 线索），让它们互斥
3. **深度限制**：`_compose_max_depth()` 按世界观调整——无限流 6 层，其他世界观保持 3
4. **多副本循环**：已由 `reusable` 字段解决——hub 豁免去重可被多次进入，BFS 深度上限防死循环

### 4. 已完成（2026-08-03 实现）

- [x] 编写 hub_plaza 模块（reusable: true，3+ 副本出口）
- [x] 编写 3 个副本模块池（生化 / 咒怨 / 修仙），每个 4 模块剧情链（入口→中段→深层→BOSS）
- [x] `web_server.py` 接入 `world=infinite_flow` → `data/modules_infinite_flow/` + `_compose_max_depth()` 深度参数化
- [x] 前端世界观下拉新增「无限流」选项
- [x] 验证：13 模块加载、validate 零问题、副本隔离不串线、BOSS 结局回 hub、web 全链路跑通
- [x] `tests/test_infinite_flow.py` 4 个专项测试，全量 190 个测试无回归

### 5. 后续待办

- [x] ~~评估：轮回者三维属性（力量/敏捷/精神）+ 强化树是否进本阶段~~ → 已实现（见第 6 节）
- [ ] 验证：主神空间 → 进副本 A → 通关 → 返回 hub → 进副本 B 的完整直播流程（需 Ollama 在线）
- [ ] 副本内容扩展：每个副本可加更多分支模块提升重玩性

---

## 第二阶段：轮回者三维属性 + 强化树（2026-07-13 实现）

### 目标

给无限流世界观加角色成长循环：轮回者三维属性（力量/敏捷/精神）+ 强化点（AP）+ 强化树，副本通关获得 AP，消费 AP 购买强化，强化以简单线性公式影响战斗数值。**不破坏 COC 模式**——Investigator 原样保留，无限流用独立的 Reincarnator 状态类。

### 数据层

- `trpg_agent/memory/game_state.py`：新增 `Reincarnator` 类
  - 字段：`name / max_hp / hp / strength / agility / spirit / ap / talents / conditions / bonus_melee / bonus_dodge / bonus_resist`
  - 线性加成公式（属性推导 + 天赋额外加成）：
    - `melee_bonus()`：力量每高出基准 2 点 +1 近战伤害，再加天赋 `bonus_melee`
    - `dodge_bonus()`：敏捷每高出基准 2 点 +1 闪避率（封顶 +20），再加天赋 `bonus_dodge`
    - `spirit_resist_bonus()`：精神每高出基准 2 点 +1 精神抗性（封顶 +20），再加天赋 `bonus_resist`
  - `GameState` 新增 `reincarnator: Reincarnator | None` 字段，序列化/反序列化完整支持（向后兼容，旧存档无此字段时为 None）
- `data/infinite_flow/talents.json`：强化树数据
  - 三线各 3 级共 9 个强化：力量线（蛮力→铁臂→破军）、敏捷线（迅捷→鬼步→幻影身法）、精神线（凝神→心如止水→不动明王）
  - 每级 cost 1 AP，逐级前置解锁，effects 声明属性加成

### 强化系统

- `trpg_agent/infinite_flow/talents.py`：`TalentCatalog`（加载/查询/购买）
  - `line_talents(line)`：按线取强化
  - `available_for(rein)`：前置满足 + 未购买 + AP 足够
  - `purchase(rein, talent_id)`：校验前置 + AP → 扣 AP → 应用 effects → 追加 talents 列表

### 战斗接入（线性公式）

- `trpg_agent/combat/resolver.py`：`CombatMechanics.__init__` 新增 `melee_bonus / dodge_bonus / spirit_resist_bonus` 三参数
  - 成功攻击伤害 = 2d6 + `melee_bonus`
  - 失败反击时按 `dodge_bonus` 概率闪避（伤害归零）
  - SAN 损失按 `spirit_resist_bonus` 概率减半（上限 50% 减免）
- `trpg_agent/combat/loop.py`：`CombatLoop` 透传三参数给 `CombatMechanics`
- `trpg_agent/combat/orchestrator.py`：`CombatOrchestrator` 新增 `reincarnator` 引用参数
  - `_bonuses()` 每次创建 CombatLoop 时动态读取轮回者加成——**强化后立即生效，无需重开 session**

### web 接入

- `trpg_agent_web/web_server.py`：
  - `event_stream` 里 `world=infinite_flow` 时创建 Reincarnator（三维 10/10/10 + 15 AP 自由分配），跳过 COC 调查员创建
  - 全局 `_sessions` 注册表：sid → Session，供强化 API 跨请求访问
  - 战斗结束结算 AP：victory +3，defeat/flee +1 保底
  - `_investigators_state_text` / `_state_snapshot` 适配轮回者
  - 新端点：
    - `GET /api/talents?session_id=`：强化树 + 轮回者状态 + 可用强化
    - `POST /api/talents/purchase`：购买强化（校验 + 应用）
- `trpg_agent_web/static/index.html`：
  - 轮回者状态卡（HP/力量/敏捷/精神/AP/已购强化）+ 强化面板按钮
  - 强化面板 modal：三线分组展示、前置锁定、已购 ✅ 标记、AP 实时显示
  - `init` 事件带 `reincarnator` 时自动隐藏 COC 调查员卡；`updateState` 同步轮回者状态

### 测试

- `tests/test_reincarnator.py`：16 个用例
  - Reincarnator 默认值/序列化 roundtrip、GameState 序列化
  - 属性加成公式（含封顶、天赋叠加、低于基准不惩罚）
  - 强化树加载（9 个、三线各 3 级）、前置校验、AP 扣减、重复拒绝、效果应用（力量+bonus 叠加）
  - 战斗透传：力量加成叠加到成功攻击伤害
- 全量 **206 个测试通过**（190 + 16 新增）零回归

### 待办

- [ ] web 在线验证：强化 API 全链路（创建流 → 查树 → 购买 → 验证属性变化）——端口 8766 曾被旧实例占用，需清端口后重启验证
- [ ] 完整直播流程验证（需 Ollama 在线）
- [ ] 副本内容扩展

