# CHANGELOG

## v1.2 — 直播演出化界面 & 手机直播变体 (2026-08-03)

### Web 前端演出化升级 (`trpg_agent_web/static/index.html`)

- 视觉风格重构为舞台化叙事布局：中心场景舞台 + 左侧调查员档案 + 右侧投票板 + 底部叙事卡
- 新增统一色彩变量与纸张质感组件（卷宗卡、投票板、胶带/污渍装饰），强化悬疑叙事氛围
- `player-host` 默认值修正为 `http://localhost:11434`（移除开发机局域网地址残留）
- 投票板标题改为「嫌疑人投票」，与直播剧情表达统一

### 叙事节奏与直播演出反馈 (`trpg_agent_web/static/index.html`)

- KP 文本流改为前端逐字渲染队列：按中文标点动态停顿，叙事更接近视觉小说节奏
- `kp_stream_end` 改为等待文本队列清空后再收尾，避免“场景先切、文字未播完”
- 投票面板新增领先项脉冲效果（`leading`）与结算盖章（`票选锁定 X`）
- 新增直播提示条 `#live-callout`：场景切换、锁票、伤害、回合完成、结局落幕等事件统一提示
- 新增镜头演出效果：`vote-focus` 聚焦推进、`hit-flash` 受击红闪、`cameraPush` 轻推镜头

### 直播模式可读性预设 (`trpg_agent_web/static/index.html`)

- 新增 `broadcast` 样式预设：仅 `mode=live` 自动启用
- 提升日志卡字号与行高、增强卡片对比与边界、调整投票板和提示条尺度，优化远距观看可读性
- 新增轮次提示（`第 x / y 轮叙事完成`），降低直播观看中的上下文丢失

### 纯手机直播页变体 (`trpg_agent_web/static/index.html`)

- 新增手机断点优化（`<=760px` 与 `<=430px 竖屏`）：舞台首屏优先、日志与投票重排、可触达性优化
- 新增 `mobile-live` 变体：仅在 `live + broadcast + 小屏` 自动启用，不影响大屏效果
- 调查员面板改为抽屉：`#mobile-roster-toggle` / `#mobile-roster-scrim`，默认收起，按需展开
- 投票出现时自动收起角色抽屉，保障手机端主交互区域（投票）不被遮挡
- 新增 `syncMobileLiveVariant()`，并在 `resize` / `orientationchange` 时同步状态

### 兼容性与影响范围

- 大屏布局与非直播模式行为保持兼容；手机变体仅在满足条件时启用
- 所有改动均为前端样式与展示逻辑增强，未变更后端 API 协议或事件字段

## v1.1 — 多世界观入口 (2026-08-01)

### 新增世界观选择 (`trpg_agent_web/web_server.py`, `static/index.html`)

- `/api/stream` 新增 `world` 参数；`coc`（默认，空值同）→ `data/modules/`，其他世界观 → `data/modules_<world>/`
- 新增 `_modules_dir_for_world()` 目录选择函数；模块池为空时返回明确 error 事件
- 前端控制栏新增「世界观」下拉（克苏鲁 COC / 哈利波特），透传 API
- 新增哈利波特模块池 `data/modules_harry_potter/`：hogwarts_entrance（入学之夜）、great_hall_gossip（晚宴流言）、forbidden_corridor（三头犬禁走廊）、troll_encounter（山怪战斗）
- 不同世界观模块池完全隔离，互不加载
- 修改记录规范：新增 `docs/development-changes.md`，每次代码变更后追加

### 战斗伤害结算修复 (`trpg_agent_web/web_server.py`)

- 战斗机制层的伤害/SAN 损失此前只存在于 `mech_result` 文本，从未写入调查员状态——已修复：伤害按人数分摊写入 HP，SAN 损失直接扣除
- 新增 `dice_roll` SSE 事件，把检定结果/伤害数值推送到前端

## v1.0 — 模块组合引擎重构 & 界面独立化 (2026-07-31)

### 模块组合引擎重构 (`trpg_agent/adventure/module_composer.py`)

**三幕加权采样系统**

将 BFS 模块组合从均匀随机改为按深度分阶段加权。每局自动遵循三幕结构：

- **序幕**（depth 0-1）：story/social 为主，combat 排除
- **发展**（depth 2-3）：investigation/horror/exploration 上升
- **高潮**（depth 4+）：combat/horror 主导

权重不是硬配额，而是彩票制——每个候选模块按类型权重持有不同数量票，高权重被抽中概率大，低级类型仍有保底票。连续同类型模块被排除（避免两个 combat 连着出）。

每出口最多 2 个候选（手写 + 随机匹配均限流），BFS 层数从 5 降为 3。从之前每局 ~110 模块降到 ~20 模块，类型分布：story 47%、combat 18%、exploration 10%、investigation 9%、social 7%、horror 7%、rest 2%

**随机战斗注入**

`_find_compatible` 中随机注入 1-2 个已解锁 combat 模块作为随机遭遇，不要求地点匹配。战斗不再只出现在手写跳转路径上。

**起步模块排除 combat**

`_pick_start` 新增 `exclude_combat` 参数，序章不会以战斗模块开局。

**战斗模块多结局死胡同修复**

战斗模块的 victory/defeat/flee 三个结局场景此前共享同一个 `last_scene.leads_to`，导致排在前面的出口（如 victory/defeat）把所有兼容下游模块抢光，排在后面的出口（如 flee）变成死胡同。修复方案：按 `exit_state.id` 定位到对应的 `combat_{outcome_id}` 独立场景，各结局独立累积自己的 leads_to/exit_labels/exit_requires。

**彩票抢占致出口零候选修复**

非战斗模块此前跨出口共享 `node_targets` 去重集合，先处理的出口会消耗全部候选导致后续出口零匹配。修复方案：战斗模块各出口用独立的 `exit_claimed` 集合去重，不再互相抢占。

### 34 个模块解锁 (`data/modules/*/module.json`)

移除以下模块的 `entry_required_clues` 限制，确保各类型均有无门槛入口：

abandoned_farm, abandoned_subway, ancestral_memory, autopsy_analysis, boarding_house, cave_entrance, churchyard_ghouls, coastal_bluffs, code_breaking, deep_one_lair, diner_gossip, docks_warehouse, dockworker_tips, fishing_village, foggy_marsh, harbor_warehouse, hidden_grove, hotel_room, journalist_interview, map_cross_reference, museum_archives, nightmare_sequence, old_quarry, patient_ward_abomination, pine_barrens_ambush, police_informant, possessed_patient, professor_study, ritual_vision, sewer_expedition, swamp_abomination, symbol_decoding, town_meeting, university_laboratory

### 战斗子系统完善

**P0 战斗机制层** — CombatMechanics (resolver.py) 代码驱动掷骰/扣血/结局判定。从选项文本解析技能检定+难度，掷 d100 判定成功/失败。成功→对敌人造成 2d6 伤害；失败→敌人反击。自动提取 SAN 损失 + 检查 all_enemies_dead 结局条件。LLM 仅负责润色叙事不做判定。

**CombatLoop 接入实战** — 回合制战斗生命周期状态机（ENTER→VOTING→RESOLVE）。submit_vote → run_mechanics（掷骰扣血）→ LLM 润色 → resolve。COMBAT_MAX_ROUNDS=6 兜底防死锁。战斗回合不消耗常规回合预算（`_non_combat_turns` 独立计数）。

**CombatOrchestrator** — 封装战斗完整生命周期（初始化/回合流转/结局跳转），web_server 战斗块从 ~140 行降至 ~70 行。队伍状态缩放：平均 HP < 40% 时敌人 HP 减半。

**战斗场景 ID 集中管理** — CombatEncounter 静态方法统一管理所有场景 ID，module_composer + web_server 不再硬编码。

**战斗摘要回流叙事** — GameState 持久化战斗历史（标题—结局—轮数—敌人），自动注入后续 LLM 系统提示词。

**过渡叙事增强** — `_transition_hint` 7 种 module_type 完整中文映射 + 8 条方向性叙事指引。

### 投票系统修复 (2026-07-29)

最初投票系统存在与 ModuleComposer/Adventure 完全脱节的问题：vote options 是房间类型硬编码关键词（搜索/调查/战斗/逃跑），与模块剧情分支无关联，投票结果无实际效果。

**v1 修复**（378c1e0）：接入模块结构。投票选项从 Adventure 场景的 exit_labels 动态生成，SSE 推送 + asyncio.Event 同步等待，/api/vote 端点驱动游戏继续。前端的 30s 倒计时 + 键盘快捷键 + 自定义选项按房间类型切换。

**v2 修复**（f298257）：场景氛围属性接入。Scene 模型新增 mood 字段，模块组合时保留作者的 exit_labels+exit_requires 到场景对象，投票分支保持与模块叙事一致。

**v3 修复**（1f73afa）：真实多票聚合。前端不再 votor 提前停表导致单人测试卡顿，改用 Queue 驱动循环每票推送 vote_tally SSE 事件；前端监听 tally 覆盖本地计数、视觉确认已投票状态。

### Web 界面 (`trpg_agent_web/static/index.html`)

- 玩家 Host 独立输入框，支持 KP 用远程 API（OpenRouter） + 玩家用本地 Ollama 同时运行
- KP Host 默认改为 `https://openrouter.ai/api/v1`
- KP 模型默认改为 `nvidia/nemotron-3-ultra-550b-a55b:free`
- 新增 `player_host` 参数传递

### Web 服务端 (`trpg_agent_web/web_server.py`)

- `event_stream` 新增 `player_host` 参数，AI 玩家流独立使用玩家 Host（不再复用 KP Host）
- `/api/stream` 端点新增 `player_host` 查询参数
- max_depth 从 5 降为 3
- `_ai_player_stream` 参数名从 `host` 改为 `player_host` 以消除歧义
- AI 模式场景过渡：KP 叙述末尾附加 `<<EXIT n>>` 标记驱动自动场景切换
- Scene.mood → BGM 联动：场景氛围自动匹配音轨

### ModuleComposer.validate() 静态校验

检查项：死线索（required_clues 无模块产出）、孤岛 location_type（entry 与全池出口无交集）、死胡同模块（无 exits + 无手写跳转 + 非结局）、起始模块可用性。实测 110 模块池 0 问题。

---

## 模块类型系统 & 战斗子系统 (2026-07-28 → 2026-07-31)

### 110 模块全量就绪 (ce7ab0c)

新增 `trpg_agent/combat/` 包：CombatLoop（状态机）、CombatEncounter/CombatEnvironment/CombatOutcome/Enemy（数据模型）、prompts（LLM 选项生成+逐轮升级+结算）。

ModuleMeta 扩展 module_type 字段（story/combat/investigation/exploration/social/horror/rest）。ModuleComposer 桥接战斗模块为 Scene + 战斗过渡场景。模块从 60 扩充至 110（含 15 个战斗遭遇）。

### 60 模块 138 场景卡全量就绪 (f7cf96c, 2026-07-29)

11 个 converge 场景补全 image_prompt+PNG，38 张 v3/v4 迭代修复（VLM 审核驱动）。线索链修复：8 模块补 provides_clues、12 模块修 location_types。500 seed 可达性 70%→100%（60/60）。VLM 审核通过率 97.8%（135/138）。

### Web 端接入模块系统 (93215a1, 2026-07-28)

`compose_modules` 参数开关模块模式。模块场景图挂载（`/images/scenes/modules/`）。前端新增模块复选框。

### 模块编写指南 (7d5b510, 2026-07-31)

`docs/module-authoring-guide.md`：7 类型完整开发指南。`docs/combat-module-guide.md`：CombatLoop API + 选项设计原则。

---

## 测试验证

- 500 seeds × depth=3：91/110 模块可达，0 错误
- 100 seeds × depth=5：110/110 模块全部可达
- ModuleComposer.validate()：110 模块池 0 问题
- Web API `/api/stream` 端点全链路通过
- 180 tests pass
