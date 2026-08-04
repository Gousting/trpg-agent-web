# 开发修改记录 (Development Changes Log)

> 每次代码变更后必须追加一条记录：改了什么、改了哪些文件、如何验证。
> 格式：日期 → 变更内容 → 涉及文件 → 验证方式 → 状态。

---

## 2026-08-04 — T7 模块改造（咒怨）：分支出口图 + 6 分支模块（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` §3.4：咒怨副本改造为分支出口图。

- 主线改造：entrance 3 出口（主线 hallway + 分支 neighbor/well）、hallway 3 出口（主线 attic + 分支 bathroom/storage）、attic 3 出口（主线 boss + 分支 diary + 撤退 hub）；hallway/attic 加 `reusable` 支持分支回边；boss 难度 3→2
- 新增 6 个分支模块：neighbor 邻居家（NPC 情报）→ deed 房契（链）、well 枯井（恐怖事件）、storage 储物间（遭遇）、bathroom 浴室（SAN）、diary 阁楼日记（削弱 BOSS 线索，呼应 BOSS rules）
- 分支模块入口加前置线索（jy_entered / jy_hallway_cleared），防止 hub 随机匹配提前连走
- **composer 修复**（发现并修复的 bug）：
  - authored 目标认领 exit 改按声明顺序（原 `_pick_exit_for_target` 对同 location_type 多 exit 全部 fallback 第 0 个，导致未认领 exit 随机匹配产生幽灵分支）
  - authored 目标去重改走 `_usable`（reusable 豁免）——支持分支模块返回主线的回边
  - `authored_only` 且声明了手写目标时整个随机匹配关闭（防幽灵分支）

### 涉及文件

| 文件 | 改动 |
|---|---|
| `data/modules_infinite_flow/dungeon_juon_{entrance,hallway,attic}/module.json` | 3 出口 + exit_labels + reusable + 难度 |
| `data/modules_infinite_flow/dungeon_juon_boss/module.json` | difficulty 3→2 |
| `data/modules_infinite_flow/dungeon_juon_{neighbor,deed,well,storage,bathroom,diary}/module.json` | 新增 6 个分支模块 |
| `trpg_agent/adventure/module_composer.py` | authored 认领顺序 / _usable 过滤 / authored_only 关闭随机 |
| `tests/test_juon_branches.py` | 新增 6 个测试：三场景出口图、分支回边、neighbor→deed 链、validate |
| `tests/test_infinite_flow.py` | 模块数 13 → 19 |

### 验证方式

- 新增 `tests/test_juon_branches.py` 6 个用例全过
- 全量回归（排除 `test_e2e_nemotron.py`）：**234 passed**（228 + 6 新增）零回归

### 状态

✅ 完成

---

## 2026-08-04 — T6 轮回者存档：跨局成长闭环（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` §4.4：Reincarnator 持久化到 `data/infinite_flow/profiles/`，副本结束（三结局任一）写入，开局可选继承——跨局成长闭环打通。

- 新增 `PROFILE_DIR` 常量 + `_save_reincarnator()` / `_load_reincarnator()` 辅助函数（roundtrip、无档/坏档容错）
- 新增 `_is_infinite_world()` 统一世界观判断
- `/api/stream` 与 `event_stream` 新增 `load_profile` 参数：为 True 时开局读档继承（属性/AP/强化，开局回满 HP）；无档则回退新建
- 战斗结束 AP 结算后自动存档轮回者
- 前端新增「继承轮回者」checkbox，勾选透传 `load_profile`

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent_web/web_server.py` | PROFILE_DIR、存档/读档/世界观判断辅助函数；load_profile 参数；结算存档 |
| `trpg_agent_web/static/index.html` | 「继承轮回者」checkbox + 透传 |
| `tests/test_reincarnator_profile.py` | 新增 5 个测试：roundtrip、JSON 关键字段、无档 None、坏档容错、自定义名 |

### 验证方式

- 新增 `tests/test_reincarnator_profile.py` 5 个用例全过
- JS 语法检查通过
- 全量回归（排除 `test_e2e_nemotron.py`）：**228 passed**（223 + 5 新增）零回归

### 状态

✅ 完成

---

## 2026-08-04 — T5 hub 选择卡片（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` §4.1：hub 副本选择显式化——卡片展示（名称/难度/通关标记）。

- `hub_plaza` 模块三个出口 label 加难度区间（生化 2-3 / 咒怨 1-2 / 修仙 3-4）
- `web_server.py`：hub 场景 vote 事件附加 `cleared_dungeons`（从 `session.state.resolved_elements` 的 `dungeon_clear_*` 线索判断）
- 前端：vote 选项含"进入副本"时渲染为副本选择卡片（大按钮 + hover 动效），已通关副本显示「✓ 已通关」

### 涉及文件

| 文件 | 改动 |
|---|---|
| `data/modules_infinite_flow/hub_plaza/module.json` | 3 个出口 label 加难度区间 |
| `trpg_agent_web/web_server.py` | hub 场景 vote 事件附加 cleared_dungeons |
| `trpg_agent_web/static/index.html` | `.hub-select` 卡片样式；showVoteBar 检测进入副本选项 + 通关标记渲染 |

### 验证方式

- JS 语法检查通过
- 全量回归（排除 `test_e2e_nemotron.py`）：**223 passed** 零回归

### 状态

✅ 完成

---

## 2026-08-04 — T4 选主控 UI：前端 leader 透传（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` T4：前端接入主控选择，配合 T3 后端 `leader` 参数。

- 控制栏新增「主控」下拉（默认(陈明)/陈明/林晓/王刚）
- `startGame()` 透传 `leader` 参数到 `/api/stream`
- 选中主控后对应角色卡高亮（金色描边 + 👑 标识），`leaderName` 全局变量
- `showVoteBar()` 动态标注投票标题：live 模式「你的行动（主控名）」/「弹幕投票 · 主控行动」，其他模式恢复「嫌疑人投票」

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent_web/static/index.html` | leader-select 下拉；`leaderName` 变量 + change 监听；startGame 透传 leader；`.char-card.leader` 高亮样式；投票标题动态标注 |

### 验证方式

- JS 语法检查：node --check 通过（提取全部 script 块）
- 全量回归（排除 `test_e2e_nemotron.py`）：**223 passed** 零回归（纯前端改动，无新增 Python 测试）

### 状态

✅ 完成

---

## 2026-08-04 — T3 队友系统：live 模式 1 主控 + 2 AI 队友（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` §5：live 模式改为「1 主控（弹幕投票驱动）+ 2 AI 队友（自动推进）」。主控固定由 `leader` 参数指定（默认陈明，T4 接前端选人 UI），其余调查员为 AI 队友。

- `event_stream()`/`/api/stream` 新增 `leader` 参数（角色名，默认 `INVESTIGATORS[0]`）
- 新增 `_ai_teammates_action()`：合并 prompt 一次生成全部队友行动（{名字: 行动}），失败兜底返回空 dict；新增 `_parse_teammates_action()` 纯解析函数（全/半角冒号、无关行忽略、空内容跳过）
- live 分支：投票窗口**期间**并行 `asyncio.create_task` 生成队友行动（隐藏延迟），投票结束后取回；队友行动逐条推送 `player_token`（speaker=队友名），并并入 KP 叙述上下文 `[队友行动]`
- 队友规则落实：不触发场景移动（移动权只归主控投票）、每人 1-2 句、失败静默兜底

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent_web/web_server.py` | `leader` 参数；`_ai_teammates_action`/`_parse_teammates_action`；live 分支并行队友任务 + 行动组装 + KP 上下文 |
| `tests/test_teammates.py` | 新增 8 个测试：解析（全/半角冒号、无关行、空内容、部分队友、空输入）、空队友、LLM 失败兜底 |

### 验证方式

- 新增 `tests/test_teammates.py` 8 个用例全过
- 全量回归（排除 `test_e2e_nemotron.py`）：**223 passed**（215 + 8 新增）零回归

### 状态

✅ 完成

---

## 2026-08-04 — T2 收益结构化：BOSS reward_ap（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` T2：BOSS 战斗结局的 AP 收益从硬编码改为模块声明字段 `reward_ap`，结算读取字段而非 LLM 口播文本。

- `CombatOutcome` 新增 `reward_ap: int = 0` 字段并解析 JSON（缺省 0，COC 模块零影响）
- 三个无限流 BOSS 模块（咒怨/生化/修仙）outcomes 声明 `reward_ap`：victory 4 / defeat 1 / flee 0
- `web_server.py` 无限流结算优先读 encounter outcome 的 `reward_ap`，读不到/为 0 时回退旧硬编码（victory 3 / defeat/flee 1）

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent/combat/encounter.py` | `CombatOutcome.reward_ap` 字段 + from_dict 解析 |
| `data/modules_infinite_flow/dungeon_{juon,rs,xiuxian}_boss/module.json` | outcomes 声明 reward_ap |
| `trpg_agent_web/web_server.py` | AP 结算改读 encounter outcome 字段，带兜底 |
| `tests/test_reward_ap.py` | 新增 4 个测试：字段解析、缺省 0、encounter 透传、三 BOSS 数据声明一致 |

### 验证方式

- 新增 `tests/test_reward_ap.py` 4 个用例全过
- 全量回归（排除 `test_e2e_nemotron.py`）：**215 passed**（211 + 4 新增）零回归

### 状态

✅ 完成

---

## 2026-08-04 — T1 出口机制：authored target 分支支持（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` T1：模块出口支持指定具体目标场景（authored target），投票选项直接映射目标。机制链路（exit_labels → 显式边 → 场景 leads_to → 投票选项）已存在，本次放开限制并加开关：

- `compose()`/`compile()`/`_build_graph()` 新增 `max_authored_branches` 参数：默认 `None`（不限制），替代原先硬编码的"authored 目标最多 2 个"缩减逻辑
- 新增 `authored_only` 参数：默认 `False` 保持原有"手写 + 随机兜底"并存行为（COC 模块依赖随机补足分支）；`True` 时被 exit_labels 认领过的 exit 不再随机匹配，出口完全由作者控制（防"声明去 B 却随机连了 X"的幽灵分支）
- `web_server.py`：`world=infinite_flow/无限流` 时 compile 传 `authored_only=True`，COC/哈利波特不受影响

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent/adventure/module_composer.py` | `max_authored_branches` + `authored_only` 参数；authored 缩减逻辑参数化；认领 exit 跳过随机匹配（仅 authored_only 时） |
| `trpg_agent_web/web_server.py` | infinite_flow 世界观 compile 传 `authored_only=True` |
| `tests/test_authored_branches.py` | 新增 5 个测试：2/3 出口可达、缩减、撤退回 hub、不串线 |

### 验证方式

- 新增 `tests/test_authored_branches.py` 5 个用例全过
- 全量回归（排除需外部 key 的 `test_e2e_nemotron.py`）：**211 passed**（206 原有 + 5 新增），COC/哈利波特/无限流零回归

### 状态

✅ 完成

---

## 2026-08-01 — 多世界观入口：哈利波特模块池

### 变更内容

新增「世界观」选择入口，支持在 COC（默认）与哈利波特两套独立模块池之间切换。核心原则：**不同世界观的模块完全隔离，互不加载、互不影响**。

- 后端新增 `world` 查询参数（`/api/stream`），`event_stream` 据此选择模块池目录
- 新增 `_modules_dir_for_world()` 函数：`coc`（或空）→ `data/modules/`，其他世界观 → `data/modules_<world>/`
- 模块池为空时返回明确错误事件，不静默失败
- 状态事件中标注当前世界观，方便前端/调试识别
- 前端新增「世界观」下拉框（克苏鲁 COC / 哈利波特），透传到 API

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent_web/web_server.py` | 新增 `_modules_dir_for_world()`；`event_stream` 与 `/api/stream` 增加 `world` 参数；模块池空校验 |
| `trpg_agent_web/static/index.html` | 控制栏新增「世界观」下拉；`startGame()` 透传 `world` 参数 |
| `data/modules_harry_potter/` | 新增 4 个哈利波特模块（`hogwarts_entrance` / `great_hall_gossip` / `forbidden_corridor` / `troll_encounter`） |

### 新增模块池结构

```
data/modules/                  ← COC 默认（110 模块，未改动）
data/modules_harry_potter/     ← 哈利波特（4 模块，独立）
  ├── hogwarts_entrance/       # 入学之夜（story，起点）
  ├── great_hall_gossip/       # 晚宴流言（social）
  ├── forbidden_corridor/      # 三头犬禁走廊（mystery）
  └── troll_encounter/         # 女盥洗室山怪（combat）
```

### 验证结果

| 验证项 | 结果 |
|---|---|
| `world=harry_potter` 模块加载 | ✅ 4 个模块，19 个场景 |
| `world=coc` 回归 | ✅ 34 个模块，121 个场景（原 110 模块池采样正常） |
| 隔离性 | ✅ 哈利波特池未加载任何 COC 模块，COC 池未加载哈利波特模块 |
| 模块池为空报错 | ✅ 返回明确 error 事件 |

### 状态

✅ 已提交。注：验证时 Ollama 不可达（宿主机服务关闭），模块组合与隔离逻辑已验证，KP 叙事流待 Ollama 恢复后复核。

## 2026-08-04 — T8 模块改造（生化）：分支出口图 + 6 分支模块（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` §3.4：生化副本改造为分支出口图，难度阶梯 2-3。

- 主线改造：entrance 3 出口（主线 corridor + 分支 armory/vent）、corridor 3 出口（主线 lab + 分支 monitor/canteen）、lab 3 出口（主线 boss + 分支 autopsy/dorm）；corridor/lab 加 `reusable` 支持分支回边；entrance 难度 1→2、lab 难度 2→3
- 新增 6 个分支模块：armory 武器库（战斗+装备）、vent 通风管道（陷阱+跳层捷径，直达 lab 且提供 rs_corridor_cleared 满足 lab 前置）、monitor 监控室（BOSS 弱点情报）、canteen 食堂（补给）、autopsy 解剖室（rs_boss_weakened 削弱 BOSS）、dorm 宿舍区（休整/背景）
- 分支模块入口加前置线索（rs_entered / rs_corridor_cleared / rs_lab_accessed），防止 hub 随机匹配提前连走

### 涉及文件

| 文件 | 改动 |
|---|---|
| `data/modules_infinite_flow/dungeon_rs_{entrance,corridor,lab}/module.json` | 3 出口 + exit_labels + reusable + 难度 |
| `data/modules_infinite_flow/dungeon_rs_{armory,vent,monitor,canteen,autopsy,dorm}/module.json` | 新增 6 个分支模块 |
| `tests/test_infinite_flow.py` | 模块数断言 19→25 |
| `tests/test_rs_dungeon_graph.py` | 新增 8 个测试：三场景出口图、分支回边、vent 跳层、BOSS 三结局回 hub、无幽灵分支、难度阶梯 |

### 验证

全量 `pytest tests/ -q --ignore=tests/test_e2e_nemotron.py`：242 passed（234 原有 + 8 新增），零回归。状态：✅ 完成

## 2026-08-04 — T9 模块改造（修仙）：分支出口图 + 6 分支模块（无限流 v2）

### 变更内容

依据 `docs/infinite-flow-v2-design.md` §3.4：修仙副本改造为分支出口图，难度阶梯 3-4。

- 主线改造：entrance 3 出口（主线 trial + 分支 library/field）、trial 3 出口（主线 danfang + 分支 arena/mentor）、danfang 3 出口（主线 boss + 分支 forbidden/court）；trial/danfang 加 `reusable` 支持分支回边；entrance 难度 1→3、trial 2→3、danfang 2→4、boss 3→4
- 新增 6 个分支模块：library 藏经阁（修炼增益）、field 灵田（采集/真相）、arena 演武场（精英战）、mentor 传功长老（剧情/执法堂暗记）、forbidden 后山禁地（危险捷径跳 boss）、court 执法堂（NPC 交互/BOSS 增益）
- **修复**：forbidden 跳层捷径首次构图丢失 boss 出口——boss 已被 danfang 主线连入且非 reusable，被 used_ids 去重过滤；给 dungeon_xiuxian_boss 加 `reusable: true`（副本终点双入口是设计意图），4 seed 验证稳定
- 分支模块入口加前置线索（xt_entered / xt_trial_passed / xt_danfang_accessed），防止 hub 随机匹配提前连走

### 涉及文件

| 文件 | 改动 |
|---|---|
| `data/modules_infinite_flow/dungeon_xiuxian_{entrance,trial,danfang}/module.json` | 3 出口 + exit_labels + reusable + 难度 |
| `data/modules_infinite_flow/dungeon_xiuxian_boss/module.json` | difficulty 3→4 + reusable |
| `data/modules_infinite_flow/dungeon_xiuxian_{library,field,arena,mentor,forbidden,court}/module.json` | 新增 6 个分支模块 |
| `tests/test_infinite_flow.py` | 模块数断言 25→31 |
| `tests/test_xt_dungeon_graph.py` | 新增 9 个测试：三场景出口图、分支回边、forbidden 跳层（含多 seed 稳定性）、BOSS 三结局回 hub、无幽灵分支、难度阶梯 |

### 验证

全量 `pytest tests/ -q --ignore=tests/test_e2e_nemotron.py`：251 passed（242 原有 + 9 新增），零回归。状态：✅ 完成


## 2026-08-04 — T10 全链路验证（无限流 v2）：deepseek-v4-flash 实跑一局

### 验证内容

依据 `docs/infinite-flow-v2-design.md` §6 T10：用真实 LLM 全链路跑一局，验证模块组合、出口机制、叙事质量、检定结算。

### 环境

- KP：deepseek-v4-flash（OpenCode Zen `/zen/go/v1`，实测 6.7-9.6s/轮）
- 玩家：ornith:9b（本地 Ollama 192.168.0.106:11434，ai 模式自动行动）
- mode=ai，world=infinite_flow，turns=8，seed=20260804

### 实测结果

1. **模块组合**：31 个模块 → 100 个场景，0.6s 完成，validate 零问题
2. **轮回者创建**：10/10/10 三维属性 + 15 AP 可分配
3. **副本入口选择**：hub 开场叙述三扇光门（绿/红/青 = 生化/咒怨/修仙），玩家进修仙
4. **出口机制全链路生效**（本轮实际路径）：
   - 青云山门（entrance）→ **藏经阁（library 分支）**——分支出口真实可选
   - 藏经阁 → 剑意试炼场（trial 主线）——分支回主线边生效
   - 试炼场 → **演武场（arena 分支）**——主线再分叉
   - 演武场 → 炼丹房（danfang 主线）——分支回主线
   - 推进至炼丹房（BOSS 前一站）
5. **检定/伤害**：2 次 dice_roll（SAN 检定）+ 1 次 damage 正常结算
6. **KP 叙事质量**：deepseek-v4-flash 全程流畅，氛围描写达标（"墙芯里有人用指节叩击"等），未见 403/推理吃满问题
7. **节奏**：ai 模式每轮约 100s（player 生成 + KP 叙述 + 检定 + 状态广播串行）；live 模式以 40s 投票窗口为主，需直播实测

### 未覆盖（有单测兜底）

- BOSS 战 + AP 结算 + 存档落盘：turns=8 未推进到 BOSS；reward_ap 结算由 T2 测试覆盖、存档由 T6 测试覆盖
- live 模式投票实感：需真实弹幕/前端，自动化用 ai 模式等价验证出口选项

### 涉及文件

无代码改动（纯验证）。参考脚本：`/tmp/t10_verify.py`（SSE 事件收集）。

### 验证

全链路跑通，出口机制/叙事/检定核心链路验证通过。状态：✅ 完成
