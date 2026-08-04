# 无限流 v2 设计文档（开发依据）

> 状态：设计定稿，待开发
> 日期：2026-08-04
> 依据：真实负载探针实测 + 现有 13 模块结构分析
> 本文件是后续开发的任务依据。开发顺序见 §6，验收标准见 §7。

---

## 1. 背景与目标

### 1.1 现状问题（代码确认）

1. **选项空泛、投票无影响**：三套副本（咒怨/生化/修仙）的全部非 BOSS 模块都只有 1 个出口（`deeper`/`to_attic`/`to_lab` 等），投票的 A/B/C 中必有两个是万能填充选项（"继续深入调查""谨慎观察四周"）。填充选项无 target，选了不移动、不给线索、不改状态。剧情完全线性，投票是形式。
2. **三角色无个性**：live 模式前端隐藏所有输入控件，三个调查员（陈明/林晓/王刚）轮流当 speaker，但行动全部来自同一个弹幕投票结果——实质是"三个人共用一次投票"，没有独立意志，主播被迫精分。
3. **内容量不足**：13 模块一局走 11-15 轮，实测节奏下撑 15-20 分钟，达不到 30 分钟直播目标。

### 1.2 实测数据（2026-08-04 探针）

| 模型 | 端点 | 400 token 输出延迟 | 备注 |
|---|---|---|---|
| gemma4:12b | 本地 Ollama 192.168.0.106 | 15.4s 均值（首轮冷启动 20.9s） | 43.8 tps |
| ornith:9b | 本地 Ollama | 9.1s | 62 tps，质量低一档 |
| qwen3.5:9b | 本地 Ollama | 12.6s | 63 tps |
| **deepseek-v4-flash** | opencode.ai/zen/go/v1（max_tokens=4000） | **6.7-9.6s**，正文 323-590 字 | 推理 token 约占 50% 预算，质量高 |

关键结论：
- **KP 模型选定 deepseek-v4-flash**（远端，~8s/轮，叙事质量高，不占本地 GPU）。本地 ornith:9b 作兜底。
- OpenCode Zen 端点对 urllib 默认 UA 返回 403，必须带浏览器/curl UA 头。
- 旧 key（sk-ee06…）余额不足；新 key 见 §8。

### 1.3 每轮（turn）耗时模型（live 模式）

单轮 = 投票窗口 + KP 叙述 + 检定分类 + 操作余量：
- KP 叙述（deepseek-v4-flash）：~8s
- 检定分类（小调用，kp_client）：~3s
- 投票窗口：**40s**（弹幕互动折中值，60s 拖节奏、30s 太紧）
- 操作/口播余量：~5s
- **合计 ≈ 56s/轮**

### 1.4 时长目标推导

- 直播目标 30 分钟；扣开局（hub 选择+初始化）与结局结算 ≈ 5 分钟 → 游戏轮 25 分钟 = 1500s
- 1500s ÷ 56s ≈ **27 轮/局**
- 模块场景轮数分布：剧情模块 2-3 轮、BOSS 3-4 轮、hub 1 轮
- 一局实际经过：hub 1 + 主线 4 + 分支 2-4 + BOSS ≈ **9-11 个模块场景**

---

## 2. 设计决策汇总

| 决策项 | 结论 |
|---|---|
| KP 模型 | deepseek-v4-flash（远端 /zen/go/v1） |
| 投票窗口 | 40s（live 模式） |
| 模块总量 | **31**（hub 1 + 每世界 10 = 4 主线 + 6 分支） |
| 单局经过 | 9-11 个模块场景（≈27 轮） |
| 世界观分层 | 咒怨 1-2（教学）/ 生化 2-3（中层）/ 修仙 3-4（高阶） |
| 角色控制 | 1 主控（弹幕投票）+ 2 AI 队友（自动推进） |
| 选项语义 | 每场景 2-3 真实出口，出口指定目标场景，选项直接影响走向 |

---

## 3. 世界观与模块分布

### 3.1 世界观难度分层

第一版三套副本是并列的，改为难度阶梯，承接轮回者成长：

| 世界观 | 难度区间 | 定位 | 机制重点 |
|---|---|---|---|
| 咒怨（dungeon_jy） | 1 → 2 | 教学层 | 投票、线索收集、SAN 系统 |
| 生化（dungeon_rs） | 2 → 3 | 中层 | 战斗资源管理、装备 |
| 修仙（dungeon_xt） | 3 → 4 | 高阶 | 数值验证、强化成型 |

- `difficulty` 字段语义化：entrance 分别 1/2/3，BOSS 分别 2/3/4（当前全为 1/3）
- hub 副本卡片按难度展示，标注推荐强度（AP 持有量）

### 3.2 模块清单（总量 31）

每世界 = 主线 4（现有，保留并加出口）+ 分支 6（新增）。

**咒怨（dungeon_jy）**
- 主线：entrance → hallway → attic → boss（伽椰子）
- 分支：neighbor 邻居家（NPC 情报）、storage 储物间（遭遇战）、well 庭院的井（恐怖事件）、bathroom 浴室（SAN 事件）、diary 阁楼日记（关键线索，可削弱 boss）、deed 房契文件（情报）

**生化（dungeon_rs）**
- 主线：entrance → corridor → lab → boss（暴君融合体）
- 分支：armory 武器库（战斗+装备）、vent 通风管道（陷阱+跳层捷径）、monitor 监控室（情报）、autopsy 解剖室（样本，可削弱 boss）、canteen 食堂（补给）、dorm 宿舍区（休整/事件）

**修仙（dungeon_xt）**
- 主线：entrance → trial → danfang → boss（入魔长老）
- 分支：library 藏经阁（修炼增益）、arena 演武场（精英战）、field 灵田（采集/休整）、forbidden 后山禁地（危险捷径）、court 执法堂（NPC 交互）、mentor 传功长老（剧情/强化）

### 3.3 模块写作规范（硬性）

1. **每场景 ≥ 2 个真实出口**（有目标场景），3 个最佳。填充选项只在出口不足时兜底（保留现有机制）。
2. **出口必须指定具体目标场景**（authored target，`模块ID::场景ID` 或等价机制），不允许"随机匹配同 location_type"。若 composer 当前不支持指定目标，先扩展 composer（见 §6 任务 T1）。
3. **分支语义**：出口分流到主线/分支/捷径三类目标，选项 label 必须让观众能感知后果差异（例："搜查储物间——可能遭遇危险但能找到线索" 而非 "继续深入调查"）。
4. **撤退出口**：每个非 BOSS 模块加"撤退回 hub"出口（provides_clues: `<世界>_retreated`，无奖励无惩罚）。BOSS 已有 victory/defeat/flee 三出口，保留。
5. **分支模块出口**：分支模块内部 1-2 个场景，出口回主线下一节点或给关键线索后自然汇合。
6. 分支模块同样遵守 location_type 隔离（同世界同类型）与线索互斥（forbidden_clues 防串线），不破坏现有隔离机制。

### 3.4 出口图（咒怨示例，其余同理）

```
hub_plaza
 ├─ to_juon → dungeon_juon_entrance
entrance（3 出口）
 ├─ a. 踏入大门 → hallway（主线）
 ├─ b. 检查信箱 → neighbor（分支：NPC 情报，给 jy_neighbor_clue）
 └─ c. 绕到屋后 → well（分支：恐怖事件，SAN 事件）
hallway（3 出口）
 ├─ a. 走向阁楼 → attic（主线）
 ├─ b. 搜查储物间 → storage（分支：遭遇战，战后给 jy_storage_clue）
 └─ c. 查看浴室 → bathroom（分支：SAN 事件）
attic（3 出口）
 ├─ a. 直面诅咒之源 → boss
 ├─ b. 阅读阁楼日记 → diary（分支：得 jy_diary_clue，boss 战削弱 -2）
 └─ c. 撤退 → hub
boss：victory / defeat / flee → hub（已有）
```

线索链保持链式锁定（required_clues），分支线索只做增强（如 diary_clue 让 boss 攻击 -2），不影响通关必需条件——保证任意分支组合都能通关，选项影响体现在"难度/收益/剧情差异"而非"卡关"。

---

## 4. 跨世界观链接

架构已正确（hub 唯一枢纽 + location_type 隔离 + 线索链锁定），补三层：

1. **hub 出口显式化**：前端加副本选择卡片（副本名/难度/已通关标记），主播点击或弹幕投票选择。hub 模块 exits 加元数据字段（difficulty_rating、requires_ap）。
2. **收益结构化**：BOSS `outcomes` 增加 `reward_ap` 字段（victory 4 / defeat 1 / flee 0），后端结算读取字段而非 LLM 口播文本（当前 victory +3 硬编码在 web_server，改为读字段；文档 §5 旧记录 +3 弃用）。
3. **撤退路径**：见 §3.3 第 4 条。
4. **轮回者存档**：Reincarnator 持久化到 `data/infinite_flow/profiles/<name>.json`——副本结束（三结局任一）时写入；开局可选"继承轮回者"（读档）。跨局成长闭环：打低层副本赚 AP → hub 买强化 → 打高层。

---

## 5. 角色系统改造（1 主控 + 2 AI 队友）

### 5.1 开场选主控

- 开局（init 后、进入副本前）选择主控角色：前端角色卡点击 或 弹幕投票（"你扮演谁"）。
- 无限流模式：主控 = 轮回者（Reincarnator，参与成长）；2 个 AI 队友为辅助角色（不参与 AP/强化，HP/技能简化，战斗用现有调查员模板数值）。
- 选定后前端标记主控（角色卡高亮 + "你"标识），投票选项标注"你的行动"。

### 5.2 每轮流程（live 模式改造）

```
1. 弹幕投票：主控角色的行动方向
   选项 = 当前场景真实出口（2-3 个），无出口时才用填充选项兜底
2. 投票窗口（40s）内，并行生成 2 个 AI 队友的行动
   （复用 _ai_player_stream，deepseek-v4-flash 或 ornith 均可，隐藏延迟）
3. 投票结束：
   主控行动 = 所选出口（驱动场景移动，移动权只在主控）
   队友行动 = AI 生成文本（只贡献叙事/检定/战斗协助，绝不触发场景移动）
4. KP 综合叙述：主控行动 + 队友反应，一次生成
```

### 5.3 队友规则（防抢戏）

- 队友行动每轮 1-2 句，聚焦"对当前场景/主控行动的反应"（发现细节、挡危险、插话）。
- 队友不替主控做决定、不触发场景移动、不消费投票。
- 队友 AI 调用失败 → 兜底为沉默或简单动作（"（林晓谨慎地观察四周）"），不阻塞主循环。
- 每轮队友调用合并为 1 次请求（一次 prompt 生成 2 人行动），控制成本与延迟。

### 5.4 战斗

- 战斗轮保持现状（CombatOrchestrator），队友作为队伍成员参与，数值用调查员模板。
- 无限流模式：主控用 Reincarnator 数值（现有透传），队友用简化模板。

---

## 6. 开发任务拆分（按依赖顺序）

- [x] **T1 出口机制扩展**：确认/扩展 composer，支持模块出口指定具体目标场景（authored target），投票选项直接映射目标。写测试（分支可达、不串线、撤退回 hub）。
  - 改动文件：`trpg_agent/adventure/module_composer.py`、`trpg_agent_web/web_server.py`、`tests/test_authored_branches.py`（详见 development-changes.md 2026-08-04 T1）
- [x] **T2 收益结构化**：BOSS outcomes 加 `reward_ap`；web_server AP 结算改为读字段；写测试。
  - 改动文件：`trpg_agent/combat/encounter.py`、`data/modules_infinite_flow/*_boss/module.json` ×3、`trpg_agent_web/web_server.py`、`tests/test_reward_ap.py`（详见 development-changes.md 2026-08-04 T2）
- [x] **T3 队友系统**：live 分支改造——主控走投票、队友走并行 AI 流；队友合并 prompt；失败兜底；写测试。
  - 改动文件：`trpg_agent_web/web_server.py`、`tests/test_teammates.py`（详见 development-changes.md 2026-08-04 T3；leader 参数已加，前端选人 UI 归 T4）
- [x] **T4 选主控 UI**：前端开场选主控（点击/投票）、角色卡高亮、投票选项标注。
  - 改动文件：`trpg_agent_web/static/index.html`（详见 development-changes.md 2026-08-04 T4）
- [x] **T5 hub 选择卡片**：前端副本卡片（名称/难度/通关标记）+ hub 模块元数据。
  - 改动文件：`data/modules_infinite_flow/hub_plaza/module.json`、`trpg_agent_web/web_server.py`、`trpg_agent_web/static/index.html`（详见 development-changes.md 2026-08-04 T5）
- [x] **T6 轮回者存档**：profiles/ 读写 + 开局继承选项 + 测试。
  - 改动文件：`trpg_agent_web/web_server.py`、`trpg_agent_web/static/index.html`、`tests/test_reincarnator_profile.py`（详见 development-changes.md 2026-08-04 T6）
- [x] **T7 模块改造（咒怨）**：现有 4 模块加真实出口/撤退口；写 6 个分支模块（neighbor/storage/well/bathroom/diary/deed）；难度字段 1-2。
  - 改动文件：`data/modules_infinite_flow/dungeon_juon_*`、`trpg_agent/adventure/module_composer.py`、`tests/test_juon_branches.py`（详见 development-changes.md 2026-08-04 T7；含 composer 分支认领 bug 修复）
- [ ] **T8 模块改造（生化）**：同上（armory/vent/monitor/autopsy/canteen/dorm）；难度 2-3。
- [ ] **T9 模块改造（修仙）**：同上（library/arena/field/forbidden/court/mentor）；难度 3-4。
- [ ] **T10 全链路直播验证**：deepseek-v4-flash 当 KP，跑"选主控 → 进副本 → 分支选择 → BOSS → 回 hub → 买强化 → 再进副本"完整流程；实测单局时长 ≥ 28 分钟。
- [ ] **T11 回归**：全量测试（现有 206+）+ 新测试，COC/哈利波特模式零回归。

---

## 7. 验收标准

1. 投票选项全部为真实出口（无出口场景除外），选择后进入目标模块——观众可见后果。
2. live 模式：1 主控由投票驱动场景移动；2 队友每轮自动行动且不抢移动权。
3. 单局（从选副本到回 hub）实测时长 28-32 分钟。
4. 三副本任意分支组合可通关；分支选择影响难度/收益/剧情（线索增强、跳层、奖励），不造成卡关。
5. 中途撤退回 hub 可行；BOSS 三结局 AP 按 reward_ap 结算。
6. 轮回者存档跨局继承，强化生效于后续副本战斗。
7. 全量测试通过，COC/哈利波特模式零回归。

---

## 8. 附录

### 8.1 端点与密钥

- OpenCode Zen：`https://opencode.ai/zen/go/v1`，模型 `deepseek-v4-flash`
- Key：`sk-YM9rZy0FSPgC8MElvPmRjfkg3sztv0jHamErKBaE7wwLHHyyeJTYyo0fgdHIqBrV`
- **坑**：urllib 默认 UA 被 403，必须带 `User-Agent: curl/8.0` 类头。
- 本地 Ollama：192.168.0.106:11434（IP 动态，每次 session 扫描确认）

### 8.2 探针脚本

- `scripts/latency_probe.py`：多端点对比探针（Ollama + Zen）
- `scripts/probe_deepseek_v4.py`：deepseek-v4-flash 专项探针（含 reasoning 验证）

### 8.3 现有模块链（改前基线）

咒怨：hub_visited → entrance(jy_entered) → hallway(jy_hallway_cleared) → attic(jy_attic_accessed) → boss → hub
生化：hub_visited → entrance(rs_entered) → corridor(rs_corridor_cleared) → lab(rs_lab_accessed) → boss → hub
修仙：hub_visited → entrance(xt_entered) → trial(xt_trial_passed) → danfang(xt_danfang_accessed) → boss → hub
