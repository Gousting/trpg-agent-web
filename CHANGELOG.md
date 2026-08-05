# CHANGELOG

## v2.3 — 无限流模块类型扩展：puzzle/social/choice/interaction + BOSS 阶段 (2026-08-05)

### 新模块类型（数据驱动，`_module_interaction_options`/`_resolve` 运行时结算）

- **puzzle 解谜**（juon_deed 地契 / rs_monitor 监控室 / xiuxian_library 藏经阁）：进入给 3 个选项，选对得线索、选错扣 HP，`{options:[{text, correct, clue, penalty}]}`
- **social 社交**（juon_neighbor 邻居 / rs_autopsy 解剖室 / xiuxian_mentor 执法堂）：NPC 话术博弈，`{responses:[{text, effect_type, success_clue, fail_text}]}`，对路话术拿关键情报、说错 NPC 敌对
- **choice 抉择**（juon_bathroom 浴室 / rs_lab 实验室 / xiuxian_court 执法堂）：道德/风险二选一，`{options:[{text, clue, hp_cost, san_cost, reward_text}]}`，无正确选项、收益代价并存
- **interaction 轻量互动**：任意类型模块的增强（juon_diary 日记 / rs_dorm 宿舍 / xiuxian_danfang 丹房），进场景给 2-3 个有实际后果的行动选项
- **BOSS 阶段机制**：`CombatEncounter.phase_thresholds`（HP 阈值触发狂暴：`{threshold: 0.6, name, attack_bonus, behavior}`，threshold 为 HP 比例），`resolve_option` 伤害后 `check_phase_triggers` 检查、提升 `attack_bonus`、返回 `phase_events`

### 交互注入

- AI 模式：进交互场景自动生成选项 → `_ai_pick_option` 选择 → `_module_interaction_resolve` 结算（`continue` 不消耗移动/检定流程，标记 `{module_id}_interacted` 防重复）
- human/live 模式：交互选项注入投票（`vote_options`），选中立即结算、不移动场景

### 验证

- 15 个模块改造（3 puzzle + 3 social + 3 choice + 3 BOSS 阶段 + 3 interaction），story 占比 19→10
- 新增 `tests/test_module_types.py` 27 用例，全量 300 passed 零回归
- 双 deepseek-v4-flash 实跑：13 轮 done 零错误，social 交互真实触发（邻居老妇人话术拿情报 jy_neighbor_trust）

## v1.14 — 无限流全量测试修复：轮回者崩溃 + 远程玩家支持 (2026-08-05)

### Bug 修复（无限流模式实测发现）

- **拾取崩溃**：`_handle_pickup` 访问 `inv_state.inventory`，`Reincarnator` 无此属性 → 第一轮拾取即 AttributeError 中断游戏流。修复：`Reincarnator` 补 `inventory` 字段 + to_dict/from_dict 序列化
- **检定崩溃**：`_dice_consequence` 访问 `inv_state.san`，轮回者无 SAN → 检定失败后崩溃、无 done 收尾。修复：无限流分支（`hasattr(san)` 判断）失败后果直接走 HP 伤害

### 功能：AI 玩家支持远程 API

- `_ai_player_stream` / `_ai_teammates_action` / `_ai_pick_option` 三处硬编码 `OllamaClient` 改为 `_make_client(player_host, ..., api_key)`，KP+玩家可同时用远程模型（如 deepseek-v4-flash 双端）
- 调用点透传 `kp_api_key`

### 验证

- 全量 273 passed 零回归
- 真实跑团（deepseek-flash KP + 本地 ornith）：19 轮完整跑到 done，战斗/检定/拾取全链路，轮回者 HP 12→8、AP 15→16
- 真实跑团（deepseek-flash 双端）：22 轮 KP/16 轮玩家跑到 done，12 次检定、5 次投票（多场战斗）、轮回者战败 HP 0 重伤撤退，速度约为本地 ornith 的 2 倍

## v1.13 — 无限流模块机制差异化：combat/rest/trap + 选项贴合剧情 (2026-08-05)

### 模块机制（阶段一）

- **combat 遭遇战**（storage 白衣怨影 / armory 感染保安 / arena 内门剑修）：标 `module_type: combat` + enemies/environment/outcomes，走现有战斗系统桥接。三出口语义：victory 回主线带线索、defeat 负伤撤回主神空间、flee 主动撤退
- **rest 补给**（canteen 食堂 / dorm 宿舍 / field 灵田）：标 `module_type: rest` + `rest:{hp_recover, san_recover}`，进入场景自动恢复（轮回者 HP；COC 全员 HP/SAN）
- **trap 陷阱**（well 枯井 / vent 通风管道 / forbidden 禁地）：标 `module_type: trap` + `trap:{check, difficulty, hp_loss, san_loss, success_clue}`，进入时 d20 检定，成功得线索、失败扣血
- 组合器 `ModuleMeta` 新增 `rest`/`trap` 字段；`web_server._module_scene_effects()` 在战斗跳转 + 投票移动两个场景切换点触发

### 选项贴合剧情

- 删除泛用填充词（"继续深入调查当前场景/谨慎地观察四周/与同伴商议"），出口不足 3 个时改用场景作者写的 `opportunities`（剧情贴合互动）
- 选中机会选项直接作为玩家行动进入检定流程（有实际后果），不再是无 target 的空选项

### 涉及

`data/modules_infinite_flow/*/module.json` ×9、`trpg_agent/adventure/module_composer.py`、`trpg_agent_web/web_server.py`、`tests/test_module_effects.py`（新增 7 用例）、出口图测试断言同步（storage/armory/arena 变 combat 场景）

### 验证

全量 273 passed 零回归；combat 三出口组合验证（victory 回主线、defeat/flee 回 hub 不死路）

## v1.12 — 无限流场景图生成 + image 字段挂载 (2026-08-05)

### 场景图 (`data/scenes/Sceneimage/inf_*.png` ×28)

- 28 张场景图按模块 `image_prompt` + 粗犷美漫插画风后缀批量生成（ComfyUI Z-Image Turbo，1344×768，KSampler 回退方案）
- qwen3.8-max 逐张 VLM 审查：26 张首轮通过，deed/mentor 因中文文字乱码重抽（prompt 弱化文字元素+风格前置），storage/dorm 首轮 API 500 重审——最终 28/28 通过
- 28 个场景挂 `image` 字段（`/images/scenes/inf_<module>_00001_.png`），前端 `scene.image` 直连生效

### 脚本

- `scripts/batch_generate_scene_images.py` — 批量出图（读模块 image_prompt → 提交 ComfyUI → 轮询下载）
- `scripts/recover_download_scene_images.py` — 轮询崩溃后的 history 恢复下载兜底
- `scripts/vlm_review_scenes.py` — qwen3.8-max 批量审查（PASS/FAIL + 评分）

## v1.11 — 无限流撤退口补齐 + entrance reusable (2026-08-05)

### 模块数据 (`data/modules_infinite_flow/*`)

- 26 个非 BOSS 模块加"撤退回主神空间"出口（`exits.flee` → hub + `exit_labels.hub_plaza`），依据 v2 设计 §3.3-4；attic 已有撤退口跳过
- 三个 entrance 模块标 `reusable: true`，支持撤退后重试同一副本
- 新增幂等批量脚本 `scripts/add_retreat_exits.py`

### 测试

- 出口图断言同步（3 出口 → 3+撤退口）：`test_juon_branches.py` / `test_rs_dungeon_graph.py` / `test_xt_dungeon_graph.py`
- 全量 266 passed 零回归；`verify_infinite_flow.py` 验证 9 seed BOSS 100% 可达

## v1.10 — 世界观驱动前端 UI + selectedMode 作用域修复 (2026-08-04)

### Web 前端 (`trpg_agent_web/static/index.html`)

- 修复真实 Bug：`showVoteBar()` 曾引用 `startGame()` 局部 `const selectedMode`，跨函数访问会抛 `ReferenceError`；改为模块级 `let currentMode` / `let currentWorld`
- 新增 `WORLD_UI_PRESETS` 配置表（`coc`/`harry_potter`/`infinite_flow`）+ `applyWorldPreset(worldValue)`：切换 `#world` 下拉或页面加载时立即刷新舞台世界标签、左侧档案标题（`#roster-title`）、投票默认标题（`.vote-title`）、手机抽屉按钮文案（`#mobile-roster-toggle`）、"继承轮回者"显隐（`#load-profile-label`），作为"预初始化层"；原有 `init` SSE 事件逻辑保留作为"运行时层"权威校正
- `showVoteBar()` 投票标题优先级：主控提示 > live 弹幕提示 > 世界观默认标题
- 设计文档：[docs/world-driven-ui-plan.md](docs/world-driven-ui-plan.md)；配置规范：[docs/frontend-world-config-guide.md](docs/frontend-world-config-guide.md)

### 测试

- `tests/test_web_server_regressions.py` 新增/更新 3 个前端字符串级回归断言（`selectedMode` 已清除、`WORLD_UI_PRESETS` 结构存在、`load-profile-label` 显隐受控），全量通过

## v1.9 — 无限流 v2 T7：咒怨模块分支改造 (2026-08-04)

### 模块数据 (`data/modules_infinite_flow/dungeon_juon_*`)

- 主线 entrance/hallway/attic 各 3 真实出口（主线 + 分支 + 撤退），hallway/attic 加 reusable 支持分支回边
- 新增 6 个分支模块（neighbor/deed/well/storage/bathroom/diary），分支入口加前置线索
- BOSS 难度 3→2

### 组合引擎 (`trpg_agent/adventure/module_composer.py`)

- 修复：authored 目标按声明顺序认领 exit；去重走 `_usable`（支持 reusable 回边）；`authored_only` 声明目标后关闭随机匹配（消灭幽灵分支）

### 测试

- 新增 `tests/test_juon_branches.py`（6 用例），全量 234 passed 零回归

## v1.8 — 无限流 v2 T6：轮回者存档 (2026-08-04)

### Web (`trpg_agent_web/web_server.py`, `static/index.html`)

- 新增 `PROFILE_DIR` + `_save_reincarnator`/`_load_reincarnator`；战斗结束自动存档，`load_profile` 参数开局继承（属性/AP/强化，HP 回满）
- 前端「继承轮回者」checkbox

### 测试

- 新增 `tests/test_reincarnator_profile.py`（5 用例），全量 228 passed 零回归

## v1.7 — 无限流 v2 T5：hub 选择卡片 (2026-08-04)

### 模块数据 (`data/modules_infinite_flow/hub_plaza/module.json`)

- 三个副本出口 label 加难度区间（生化 2-3 / 咒怨 1-2 / 修仙 3-4）

### Web (`trpg_agent_web/web_server.py`, `static/index.html`)

- hub 场景 vote 事件附加 `cleared_dungeons`（从线索状态判断已通关副本）
- 前端 hub 场景渲染副本选择卡片，已通关显示「✓ 已通关」

### 测试

- JS 语法检查通过，全量 223 passed 零回归

## v1.6 — 无限流 v2 T4：选主控 UI (2026-08-04)

### Web 前端 (`trpg_agent_web/static/index.html`)

- 控制栏新增「主控」下拉，选中角色卡金色高亮 + 👑 标识
- `startGame()` 透传 `leader` 参数；live 模式投票标题动态标注「你的行动（主控名）」

### 测试

- JS 语法检查通过，全量 223 passed 零回归

## v1.5 — 无限流 v2 T3：队友系统 (2026-08-04)

### Web (`trpg_agent_web/web_server.py`)

- `/api/stream` 新增 `leader` 参数：live 模式固定主控（投票驱动移动），其余 2 名调查员为 AI 队友
- 新增 `_ai_teammates_action`（合并 prompt 一次生成全部队友行动）+ `_parse_teammates_action`（容错解析）
- 队友行动在投票窗口内并行生成（隐藏延迟），逐条推送前端并并入 KP 叙述上下文；失败静默兜底

### 测试

- 新增 `tests/test_teammates.py`（8 用例），全量 223 passed 零回归

## v1.4 — 无限流 v2 T2：收益结构化 (2026-08-04)

### 战斗结算 (`trpg_agent/combat/encounter.py`, `trpg_agent_web/web_server.py`)

- `CombatOutcome` 新增 `reward_ap` 字段，BOSS 结局 AP 收益由模块声明（victory 4 / defeat 1 / flee 0），web_server 结算读字段、缺省回退旧值
- 三个无限流 BOSS 模块（咒怨/生化/修仙）outcomes 已声明 reward_ap

### 测试

- 新增 `tests/test_reward_ap.py`（4 用例），全量 215 passed 零回归

## v1.3 — 无限流 v2 T1：出口机制 (2026-08-04)

### 模块组合引擎 (`trpg_agent/adventure/module_composer.py`)

- `compose()`/`compile()`/`_build_graph()` 新增 `max_authored_branches`（默认不限）与 `authored_only`（默认 False）参数
- 模块最后场景 `exit_labels` 手写目标不再被硬编码砍到 2 个；`authored_only=True` 时出口完全由作者控制（无限流），认领过的 exit 跳过随机匹配，消灭"幽灵分支"
- 默认行为不变：COC/哈利波特仍走"手写 + 随机兜底"并存

### Web (`trpg_agent_web/web_server.py`)

- `world=infinite_flow` 时模块组合启用 `authored_only=True`

### 测试

- 新增 `tests/test_authored_branches.py`（5 用例），全量 211 passed 零回归

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

## v2.0 — 无限流 v2 T8：生化模块分支改造 (2026-08-04)

### 模块数据 (`data/modules_infinite_flow/dungeon_rs_*`)

- 主线 entrance/corridor/lab 各 3 真实出口（主线 + 分支），corridor/lab 加 reusable 支持分支回边
- 新增 6 个分支模块（armory/vent/monitor/canteen/autopsy/dorm），vent 为跳层捷径直达 lab
- 难度阶梯 2-3：entrance 1→2、lab 2→3
- BOSS 三结局回 hub（T2 已加 reward_ap）

### 测试

- 新增 `tests/test_rs_dungeon_graph.py`（8 用例），更新模块数断言 19→25，全量 242 passed 零回归

## v2.1 — 无限流 v2 T9：修仙模块分支改造 (2026-08-04)

### 模块数据 (`data/modules_infinite_flow/dungeon_xiuxian_*`)

- 主线 entrance/trial/danfang 各 3 真实出口（主线 + 分支），trial/danfang 加 reusable
- 新增 6 个分支模块（library/field/arena/mentor/forbidden/court），forbidden 跳层捷径直达 boss
- 难度阶梯 3-4：entrance 1→3、trial 2→3、danfang 2→4、boss 3→4（boss 加 reusable 支持双入口）

### 测试

- 新增 `tests/test_xt_dungeon_graph.py`（9 用例，含跳层多 seed 稳定性），更新模块数断言 25→31，全量 251 passed 零回归

## v2.2 — 无限流 v2 T10：全链路验证 (2026-08-04)

- 用 deepseek-v4-flash（远端 KP）+ ornith:9b（本地 AI 玩家）实跑一局无限流
- 31 模块 → 100 场景组合成功；轮回者创建正常
- 出口机制全链路验证：青云山门→藏经阁（分支）→试炼场→演武场（分支）→炼丹房，主线/分支 zigzag 完整走通
- 检定/伤害结算正常，KP 叙事质量达标
- BOSS/AP/存档有 T2/T6 单测兜底，live 模式需直播实测
