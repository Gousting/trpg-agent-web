# CHANGELOG

## v1.0 — 模块组合引擎重构 & 界面独立化 (2026-07-13)

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

### 34 个模块解锁 (`data/modules/*/module.json`)

移除以下模块的 `entry_required_clues` 限制，确保各类型均有无门槛入口：

abandoned_farm, abandoned_subway, ancestral_memory, autopsy_analysis, boarding_house, cave_entrance, churchyard_ghouls, coastal_bluffs, code_breaking, deep_one_lair, diner_gossip, docks_warehouse, dockworker_tips, fishing_village, foggy_marsh, harbor_warehouse, hidden_grove, hotel_room, journalist_interview, map_cross_reference, museum_archives, nightmare_sequence, old_quarry, patient_ward_abomination, pine_barrens_ambush, police_informant, possessed_patient, professor_study, ritual_vision, sewer_expedition, swamp_abomination, symbol_decoding, town_meeting, university_laboratory

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

### 测试验证

- 500 seeds × depth=3：91/110 模块可达，0 错误
- 100 seeds × depth=5：110/110 模块全部可达
- Web API `/api/stream` 端点正常：模块组合 → 地图生成 → 调查员初始化 → 流式推送全链路通过
