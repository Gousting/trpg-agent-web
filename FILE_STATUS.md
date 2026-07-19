# TRPG Agent — 文件状态清单

基于 DMbot (Pr0degie/dungeonmaster) 搬运，为中文 COC 跑团适配。

## 状态标记

- ✅ 直接可用
- 🔧 需要中文翻译（代码逻辑干净，德语文本需替换）
- 🚧 需要架构适配（Discord/WH40k/德语解耦后可用）
- ❌ 待重写/丢弃

---

## LLM 模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `llm/client.py` | ✅ | Ollama 异步客户端，直接可用 |
| `llm/preflight.py` | ✅ | Ollama 连接检查 |
| `llm/persona.py` | ✅ | System prompt 加载器 |
| `llm/prompt_assembly.py` | 🔧 | 分层拼接器，"Was bisher geschah"→"前情提要" |
| `llm/stream_assembler.py` | 🚧 | 流式组装器，依赖 sanitize/marker/textsplit |
| `llm/sanitize.py` | 🔧 | DM 回答清洗，德国正则→中文正则 |
| `llm/consistency.py` | 🔧 | 一致性守卫，德语动词判断→中文方案 |
| `llm/echo_guard.py` | 🔧 | 回声守卫，德语标签→中文标签 |
| `llm/intro_guard.py` | 🔧 | 开场守卫 |
| `llm/director_msgs.py` | 🔧 | DM 开场引导，德文→中文 |
| `llm/roll_router.py` | 🔧 | 掷骰路由，德语 prompt→中文 prompt |

## 规则模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `rules/engine.py` | ✅ | 掷骰引擎（纯数学），但 COC 判定逻辑需新增 |
| `rules/profile.py` | 🔧 | 规则 Profile 框架，IM→COC |
| `rules/marker.py` | 🔧 | 标记解析，`<<TEST>>`→`<<检定>>` |
| `rules/characters.py` | 🚧 | 角色系统，psyker→删，加 SAN/克苏鲁神话 |
| `rules/combat.py` | 🚧 | 战斗模块，Warp Charge→删 |
| `rules/summary.py` | 🔧 | 规则摘要，德语→中文 |

## 记忆模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `memory/history.py` | ✅ | 对话历史持久化 (Phase 2 重写为 HistoryStore) |
| `memory/game_state.py` | ✅ | COC 游戏状态 (Phase 2 新增: Investigator/Npc/Quest) |
| `session.py` | ✅ | Session 管理器 (Phase 2 新增: 角色加载/历史/上下文/prompt) |
| `memory/state.py` | 🔧 | WorldState 数据层，德语标签→中文标签 |
| `memory/chekhov.py` | 🔧 | Chekhov 清单，德语→中文 |
| `memory/npc_memory.py` | 🔧 | NPC 记忆，德语→中文 |
| `memory/recap.py` | 🔧 | Recap 生成，德语→中文 |
| `memory/gametime.py` | 🔧 | 游戏内时间，德语→中文 |

## 核心模块

| 文件 | 状态 | 说明 |
|------|------|------|
| `orchestrator.py` | 🚧 | DMBrain，Discord 解耦后可用核心逻辑 |
| `logsetup.py` | ✅ | 日志配置 |
| `turn_timing.py` | ✅ | 回合计时 |
| `shutdown.py` | ✅ | 优雅关闭 |

## Prompt 文件

| 文件 | 状态 | 说明 |
|------|------|------|
| `prompts/dm_core_de.md` | 🔧 | 核心 DM 人格，德文→中文，COC 化 |
| `prompts/campaign_tone_de.md` | ❌ | WH40k 基调，丢弃 |
| `prompts/chekhov_extract_de.md` | 🔧 | Chekhov 提取，德文→中文 |
| `prompts/npc_memory_extract_de.md` | 🔧 | NPC 记忆提取，德文→中文 |

---

## 下一轮操作顺序

1. `llm/sanitize.py` — 中文正则（P0，直接影响输出质量）
2. `prompts/dm_core_de.md` → `prompts/dm_core_zh.md` — 中文 KP 人格（P0）
3. `rules/profile.py` — COC 7 版 SystemProfile JSON（P0）
4. `llm/consistency.py` — 中文一致性守卫（P1）
5. `memory/state.py` — 中文标签（P1）
6. `orchestrator.py` — 解耦 Discord（P1）
7. 其余 🔧 文件逐步翻译
