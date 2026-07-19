# 后续迭代方案

## Phase 1 ✅ — 纯文字闭环

**目标：** 跑通"玩家说 → KP 回"的最小闭环，验证 LLM 能做 DM。

**已完成：**
- 中文 KP 人格 prompt（`kp_core_zh.md`）
- KP 回答清洗管道（`sanitize.py`）
- COC 7 版检定引擎（`rules/coc.py`）
- Ollama 异步客户端（`llm/client.py`）
- 39 项单元测试 + 全链路集成测试

**验证标准：** 三轮对话无崩，KP 正确融入检定结果，氛围叙述达标。

---

## Phase 2 — 游戏状态与多轮记忆

**目标：** 让 KP 记住之前发生的事，维护角色状态。

**任务：**

1. **中文化 `memory/state.py`** — WorldState 数据层，德语标签→中文标签。`DOWNED_CONDITION` → `"失去行动能力"`，`ATTITUDE_SCALE` 五级量表→中文。

2. **中文化 `memory/history.py`（已完成）** — 对话历史持久化，JSONL 格式。直接可用。

3. **中文化 `prompt_assembly.py`（已完成）** — "前情提要"标题已改。

4. **连接记忆到 pipeline** — 每轮对话自动追加 history，超出上下文窗口时触发 recap 压缩。

5. **角色卡加载** — 从 JSON 文件加载调查员属性/技能，检定时自动查表。

**验证标准：** 五轮以上对话不丢失关键信息，KP 能引用之前提到过的 NPC 和事件。

---

## Phase 3 — 掷骰路由

**目标：** KP 叙述中需要检定时，自动判断并调引擎。

**任务：**

1. **中文化 `llm/roll_router.py`** — 独立的掷骰分类器。判断"玩家这个行动要不要过检定、什么技能、什么难度"。

2. **接入 COC 引擎** — router 输出 → `resolve_coc()` → 结果注入下一轮 prompt。

3. **标记解析** — `<<检定 侦查 困难 对 陈明>>` 格式的备用标记支持。

**验证标准：** KP 说出"你需要过个侦查检定"，结果正确反映在后续叙述中。

---

## Phase 4 — 完整游戏系统

**目标：** 支持完整的 COC 跑团 session。

**任务：**

1. **SAN 值系统** — 理智检定、临时疯狂、不定疯狂
2. **战斗轮** — COC 战斗结算（格斗/射击/闪避/伤害）
3. **幸运值** — 消耗幸运值调整检定
4. **孤注一掷** — 允许重试失败检定（后果更严重）
5. **中文化 `memory/npc_memory.py`** — NPC 记忆，NPC 态度漂移
6. **中文化 `memory/chekhov.py`** — Chekhov 清单（未收束伏线）

**验证标准：** 跑完一个完整的 COC 快速开始模组（如《古屋疑云》）。

---

## Phase 5 — 语音链路

**目标：** 用语音交互替代打字。

**任务：**

1. **STT（语音转文字）** — FunASR + CAM++，中文多人识别
2. **TTS（文字转语音）** — Edge TTS 或 XTTS 中文语音
3. **VAD（语音活动检测）** — silero-vad，自动切句

**验证标准：** 说→听→说的延迟可控，多人说话不混淆。

---

## Phase 6 — 平板客户端

**目标：** HTML 页面，录音 + 显示。平板浏览器打开即可，零安装。

**任务：**

1. Web Audio API 录音 → WebSocket 发送
2. KP 回答实时显示
3. 角色卡/检定结果显示

---

## 中文化待办清单

以下 15 个文件搬运自 DMbot，代码逻辑可用但德语文本需翻译：

| 优先级 | 文件 | 当前状态 |
|--------|------|----------|
| P1 | `llm/consistency.py` | 一致性守卫，德语动词判断→中文方案 |
| P1 | `memory/state.py` | WorldState 数据层 |
| P1 | `memory/recap.py` | Recap 生成 |
| P2 | `memory/npc_memory.py` | NPC 记忆 |
| P2 | `memory/chekhov.py` | Chekhov 清单 |
| P2 | `memory/gametime.py` | 游戏内时间 |
| P2 | `llm/echo_guard.py` | 回声守卫 |
| P2 | `llm/intro_guard.py` | 开场守卫 |
| P2 | `llm/director_msgs.py` | DM 开场引导 |
| P2 | `rules/summary.py` | 规则摘要展示 |
| P2 | `rules/marker.py` | 标记解析 |
| P2 | `prompts/chekhov_extract_de.md` | Chekhov 提取 prompt |
| P2 | `prompts/npc_memory_extract_de.md` | NPC 记忆提取 prompt |
| P3 | `orchestrator.py` | DM 大脑，需 Discord 解耦 |
| P3 | `llm/stream_assembler.py` | 流式组装器 |
