# TRPG Agent — 文件状态清单

基于 DMbot (Pr0degie/dungeonmaster) 搬运，为中文 COC 跑团适配。

## 状态标记

- ✅ 直接可用 / 已完成中文化
- 🔧 需要中文翻译（代码逻辑干净，德语文本需替换）
- 🚧 需要架构适配（Discord/WH40k/德语解耦后可用）
- ❌ 待重写/废弃

---

## Phase 1-2 完成

| 文件 | 状态 | 说明 |
|------|------|------|
| `llm/sanitize.py` | ✅ | 中文 KP 回答清洗 (12 项正则模式) |
| `prompts/kp_core_zh.md` | ✅ | 守秘人核心人格 (52 行, 2587 字) |
| `rules/coc.py` | ✅ | COC 7 版检定引擎 |
| `rules/engine.py` | ✅ | 通用掷骰引擎 |
| `llm/client.py` | ✅ | Ollama 异步客户端 |
| `llm/persona.py` | ✅ | 中文 prompt 加载 |
| `llm/prompt_assembly.py` | ✅ | Prompt 组装 |
| `data/systems/coc_7e.json` | ✅ | COC 规则系统 Profile |
| `session.py` | ✅ | Session 管理器 |
| `memory/game_state.py` | ✅ | COC 游戏状态 |
| `memory/history.py` | ✅ | 对话历史 |
| `data/sessions/default/characters.json` | ✅ | 示例角色卡 (3 调查员 + 2 NPC) |

## Phase 3 完成

| 文件 | 状态 | 说明 |
|------|------|------|
| `llm/roll_router.py` | ✅ | 检定分类器 (中文化, constrained JSON) |

## Phase 4 完成

| 文件 | 状态 | 说明 |
|------|------|------|
| `rules/sanity.py` | ✅ | SAN 检定、临时/不定疯狂症状表 |
| `rules/combat.py` | ✅ | COC 战斗结算（重写，原 WH40k 代码已替换） |
| `rules/luck.py` | ✅ | 幸运值消耗与恢复 |
| `rules/pushing.py` | ✅ | 孤注一掷重试 |
| `memory/npc_memory.py` | ✅ | NPC 记忆（德语→中文翻译完成） |
| `memory/chekhov.py` | ✅ | Chekhov 清单（德语→中文翻译完成） |
| `memory/recap.py` | ✅ | Recap 生成（德语→中文翻译完成） |
| `llm/echo_guard.py` | ✅ | 回声守卫（德语→中文翻译完成） |
| `llm/director_msgs.py` | ✅ | DM 开场引导（德语→中文翻译完成） |
| `rules/summary.py` | ✅ | 规则摘要（德语→中文翻译完成） |
| `prompts/chekhov_extract_zh.md` | ✅ | Chekhov 提取 prompt（中文版） |
| `prompts/npc_memory_extract_zh.md` | ✅ | NPC 记忆提取 prompt（中文版） |
| `tests/test_phase4.py` | ✅ | 20 项规则测试 |
| `tests/test_integration.py` | ✅ | 6 轮全链路集成测试 |
| `memory/gs_parser.py` | ✅ | GS 标记解析 — KP 回复中 `<!--GS-->` 块自动写入 GameState (11 种指令) |
| `memory/database.py` | ✅ | SQLite 持久层 — 6 表 WAL 模式，跨 session 调查员复用，声纹绑定，快照存档 |
| `tests/test_multiplayer.py` | ✅ | 7 项多人联机 + 存档测试 |
| `tests/test_database.py` | ✅ | 6 项数据库集成测试 |

## OBS 覆盖层 + 直播素材

| 文件 | 状态 | 说明 |
|------|------|------|
| `trpg_agent/overlay_server.py` | ✅ | WebSocket 推送服务，REST API 控制场景/骰子/角色/弹幕/投票 |
| `docs/overlay_b.html` | ✅ | 哥特恐怖风 OBS 浏览器覆盖层，1920×1080，3:1 列比 |
| `docs/scene_bg.png` | ✅ | 密大禁书区场景卡（ComfyUI Z-Image + Ink Frenzy 生成） |
| `docs/layout-mockup.html` | ✅ | 布局原型 A（已废弃，迭代到 overlay_b） |
| `docs/layout-mockup-b.html` | ✅ | 布局原型 B（迭代到 overlay_b） |
| `docs/layout-mockup-c.html` | ✅ | 布局原型 C（迭代到 overlay_b） |
| `docs/bilibili-draft.md` | ✅ | B站直播文案草稿 |

## 未处理 — DMbot 遗留文件

### LLM 模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `llm/consistency.py` | 🔧 | 一致性守卫，德语动词判断→中文方案 |
| `llm/intro_guard.py` | 🔧 | 开场守卫，德语→中文 |
| `llm/stream_assembler.py` | 🚧 | 流式组装器，依赖 sanitize/marker/textsplit |

### 规则模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `rules/marker.py` | 🔧 | 标记解析 |
| `rules/characters.py` | 🚧 | 角色系统，psyker→删 |

### 记忆模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `memory/state.py` | ❌ | 已被 game_state.py 替代，可废弃 |
| `memory/gametime.py` | 🔧 | 游戏内时间，德语→中文 |

### 其他

| 文件 | 状态 | 说明 |
|------|------|------|
| `orchestrator.py` | 🚧 | DMBrain，Discord 解耦后可用 |
| `logsetup.py` | ✅ | 日志配置 |
| `turn_timing.py` | ✅ | 回合计时 |
| `shutdown.py` | ✅ | 优雅关闭 |
| `tts/textsplit.py` | 🔧 | TTS 文本分割，待 Phase 5 |
