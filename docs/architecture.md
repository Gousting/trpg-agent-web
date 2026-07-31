# TRPG Agent Web — 架构文档

> 更新：2026-07-31 | 基于 `56d2b2a`（含 live 直播模式，已合入 v1.0 分支）

## 项目概览

本地优先的克苏鲁 TRPG AI 跑团系统。KP（守秘人）和调查员均由本地 Ollama 模型扮演，Web 前端通过 SSE 实时推送叙事流，支持 AI 自动 / 人类玩家 / 直播三种模式，110 个可组合剧情模块驱动沙盒叙事。

- **语言**：Python 3.11
- **LLM**：Ollama (ornith:9b / gemma4:12b / qwen3.6-27b)
- **Web**：FastAPI + SSE + 纯 HTML/JS 前端
- **存储**：SQLite + JSON 文件
- **依赖管理**：uv + pyproject.toml

> 注：以下文件行数、模块统计等数字截至上述提交，可能随后续开发浮动。

---

## 目录结构

```
trpg-agent-web/
├── trpg_agent_web/             # Web 层
│   ├── web_server.py           # ★ FastAPI 入口 + SSE 游戏循环 (1316行)
│   └── static/
│       └── index.html          # ★ 前端 SPA (881行)
│
├── trpg_agent/                 # 核心引擎
│   ├── session.py              # Session 管理器 (1090行)
│   │
│   ├── llm/                    # LLM 调用层
│   │   ├── client.py           # Ollama HTTP 客户端 (184行)
│   │   ├── prompt_assembly.py  # Prompt 组装（状态→上下文）
│   │   ├── stream_assembler.py # 流式输出拼接
│   │   ├── preflight.py        # 启动前系统检查
│   │   ├── consistency.py      # 输出一致性校验
│   │   ├── director_msgs.py    # 导演系统消息生成
│   │   ├── echo_guard.py       # 防止 LLM 复读
│   │   ├── intro_guard.py      # 防止开场白重复
│   │   ├── roll_router.py      # 骰子→叙述路由
│   │   ├── sanitize.py         # 输出清洗
│   │   ├── persona.py          # 调查员人设管理
│   │   └── remote_client.py    # 远程 API 兼容
│   │
│   ├── memory/                 # 记忆与状态
│   │   ├── state.py            # ★ 世界状态（硬事实）(865行)
│   │   ├── history.py          # 对话历史包装
│   │   ├── database.py         # SQLite 持久层
│   │   ├── game_state.py       # 游戏状态快照
│   │   ├── gs_parser.py        # 游戏状态解析
│   │   ├── gametime.py         # 游戏内时间
│   │   ├── chekhov.py          # 契诃夫之枪追踪
│   │   ├── npc_memory.py       # NPC 独立记忆
│   │   └── recap.py            # 回顾/摘要生成
│   │
│   ├── combat/                 # 战斗系统
│   │   ├── orchestrator.py     # ★ 战斗编排器 (188行)
│   │   ├── loop.py             # 回合调度器
│   │   ├── encounter.py        # 遭遇战生成
│   │   ├── resolver.py         # 战斗结果判定
│   │   └── prompts.py          # 战斗 Prompt
│   │
│   ├── adventure/              # 模块系统
│   │   ├── module_composer.py  # ★ 模块组合引擎 (1289行)
│   │   └── variance.py         # 随机变量注入
│   │
│   ├── rules/                  # COC 规则引擎
│   │   ├── engine.py           # ★ 骰子+检定核心 (186行，2026-07-31 已清理死代码)
│   │   ├── coc.py              # COC 7e 规则实现
│   │   ├── characters.py       # 角色属性管理
│   │   ├── combat.py           # 战斗规则
│   │   ├── sanity.py           # 理智值系统
│   │   ├── luck.py             # 幸运值系统
│   │   ├── pushing.py          # 孤注一掷
│   │   ├── marker.py           # 标记系统
│   │   ├── profile.py          # 角色档案
│   │   └── summary.py          # 属性摘要
│   │
│   ├── mapgen.py               # Roguelike 地图生成
│   ├── scene_integration.py    # 场景图片集成
│   ├── scene_matcher.py        # 场景匹配
│   ├── overlay_server.py       # OBS 直播覆盖层
│   ├── turn_timing.py          # 回合计时
│   ├── logsetup.py             # 日志配置
│   ├── rag/                    # RAG 检索
│   ├── tools/                  # 工具调用
│   ├── tts/                    # 文字转语音
│   └── __main__.py             # CLI 入口
│
├── data/
│   ├── modules/                # 110 个 JSON 剧情模块
│   ├── adventures/             # 手写冒险场景
│   ├── characters/             # 角色头像
│   ├── items/                  # 物品图片
│   ├── scenes/                 # 场景背景图
│   ├── bgm/                    # 背景音乐
│   ├── sessions/               # 游戏存档
│   ├── saves/                  # 测试存档
│   └── systems/                # 规则系统定义
│
├── prompts/                    # Prompt 模板
│   ├── kp_core_zh.md           # KP 核心 Prompt
│   ├── npc_memory_extract_zh.md
│   └── chekhov_extract_zh.md
│
├── tests/                      # 测试 (180 pass / 0 fail，另有 test_e2e_nemotron.py 依赖外部环境不参与常规收集)
├── docs/                       # 文档
│   ├── live-mode-plan.md
│   ├── module-authoring-guide.md
│   └── ...
├── scripts/                    # 辅助脚本
├── pyproject.toml
└── README.md
```

---

## 核心数据流

```
浏览器 ──GET /api/stream──→ FastAPI (web_server.py)
                               │
                    ┌──────────┴──────────┐
                    │   event_stream()    │
                    │  SSE 生成器/游戏主循环 │
                    │ （回合推进、模式分发、  │
                    │  KP 叙事、玩家行动 均  │
                    │  在此函数内直接完成，  │
                    │  无独立编排器类）      │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┬──────────────────┐
              ▼                ▼                 ▼                  ▼
     Session (session.py)  llm/client.py     rules/ 规则引擎    combat/orchestrator.py
     - 状态加载/保存        - Ollama 调用     - 骰子检定           （仅战斗场景时）
     - 对话历史             - 流式输出         - 技能判定          - 遭遇战生成
     - NPC 记忆                                - 伤害计算          - 战斗回合调度
```

**注**：`event_stream()` 是唯一的游戏主循环，直接调用 `Session`/LLM 客户端/规则引擎，不存在独立的顶层"Orchestrator"编排器类（历史上有过一个 `trpg_agent/orchestrator.py`，944 行的 Discord 机器人式回合编排器，2026-07-31 确认全仓库零调用方后已删除）。战斗场景是唯一被拆成独立编排器模块的部分，见 `combat/orchestrator.py`。

### KP 叙述质量守卫（2026-07-31 接入）

`orchestrator.py` 被删除前包裹调用过几个纯函数式的输出质量模块（`llm/sanitize.py`/`llm/echo_guard.py`/
`llm/consistency.py`/`llm/intro_guard.py`），删除后一度全部处于孤立状态。现已接入 `event_stream()` 的
KP 叙述环节（开场白 + 常规每回合叙述，战斗环节的进场/结算叙述暂未接入，避免打乱其结构化解析）：

```
LLM 整段生成(_chat_generate) → _sanitize() 清洗 → 质量检查(_check_kp_narration)
                                                        │
                                        ┌───────────────┼───────────────┐
                                        ▼               ▼               ▼
                                  echo_guard      consistency      intro_guard
                                 复读/自我重复    NPC 说话矛盾      开场白弱质量
                                        │               │               │
                                        └───────────────┴───────────────┘
                                                        │ 命中 → 追加中文修正指令，重新生成一次
                                                        ▼
                                          _fake_stream() 分块 + 微延迟发送（前端 kp_token 事件不变）
```

- 由于改成了"整段生成后再发送"，违规时可以真正重新生成一次（原设计里流式路径只能事后记录，无法撤回已发送文字）。
- `consistency.check()` 的"死亡 NPC 说话"检查目前恒为安全空操作：`GameState.Npc`（本项目当前 COC 系统）没有
  wounds/hp 字段，不跟踪 NPC 生死；"NPC 不在场景中却说话"这一半用 `scene.npcs_here` 比对是真正生效的。
- `echo_guard`/`intro_guard`/`consistency` 原本的 nudge 文案是德语（旧 Discord 版遗留），已新增中文版本
  （`ECHO_NUDGE_ZH`/`REPEAT_NUDGE_ZH`/`INTRO_RETRY_NUDGE_ZH`/`retry_nudge_zh()`）；`consistency.py` 的语音归属
  正则也补充了中文动词/引号，原德语正则原样保留、互不影响。

### 三种模式的数据流差异

| 阶段 | AI 模式 | Human 模式 | Live 模式 |
|---|---|---|---|
| 选项生成 | — | ✅ `vote` SSE | ✅ `vote` SSE |
| 玩家行动 | Ollama 扮演调查员 | 前端投票等待 | 前端投票等待 |
| 超时处理 | — | 默认 `choice="a"` | `_ai_pick_option()` |
| 投票窗口 | — | 32s (VOTE_WINDOW_SECONDS) | 90s (LIVE_VOTE_SECONDS) |

### SSE 事件类型

| 事件 | 含义 | 关键字段 |
|---|---|---|
| `init` | 游戏初始化 | `mode`, `vote_seconds`, `investigators`, `room` |
| `kp_stream_start/end` | KP 叙事流 | `speaker`, `text` |
| `player_stream_start/end` | 调查员行动流 | `speaker`, `action` |
| `vote` | 展示投票选项 | `options`, `vote_seconds`, `session_id` |
| `vote_tally` | 实时票数更新 | `tally` |
| `ai_pick` | AI 自动选择 | `choice`, `label` |
| `status` | 状态更新 | `hp`, `san`, `inventory` |
| `map` | 地图更新 | `image` (base64) |
| `done` | 游戏结束 | — |

---

## 关键模块职责

### Web 层 (`trpg_agent_web/`)

**`web_server.py`** — 唯一入口。FastAPI app + SSE 流 + 投票 API + 图片静态服务。核心函数 `event_stream()` 是游戏主循环：KP 叙事 → 玩家行动 → 状态更新 → 下一回合，通过 SSE 逐步推送。投票系统基于 asyncio.Queue，支持多客户端并发投票（直播场景）。

**`static/index.html`** — 纯前端 SPA。SSE 事件处理、投票 UI、地图渲染、角色面板、TTS 播放。模式下拉支持 AI 自动 / 人类玩家 / 直播三种。

### 核心引擎 (`trpg_agent/`)

**`session.py`** — 生命周期管理。加载/保存/恢复游戏会话，连接记忆、状态和历史，为每回合提供完整上下文。（2026-07-31 已删除 `orchestrator.py`：944 行的 Discord 机器人式回合编排器，全仓库 grep 确认零调用方，真正的回合循环完全在 `web_server.py` 的 `event_stream()` 里内联实现。）

**`llm/client.py`** — Ollama HTTP 客户端包装。`OllamaClient` 类提供 `chat()` 方法，支持流式输出和独立上下文窗口。

**`llm/prompt_assembly.py`** — 将世界状态、对话历史、角色人设、规则参考组装成每次 LLM 调用的 prompt。

**`memory/state.py`** — 世界状态（`WorldState`）——"硬事实"存储中不属于单个调查员的部分：NPC 记忆、任务/时限（`Quest`/`Deadline`/`Clock`）、场景解决标记（`scene_flags`）、战斗中的临时 `Combatant`。**注意**：调查员自身的 HP/SAN/技能/物品实际存在 `memory/game_state.py` 的 `Investigator` 类中，契诃之枪追踪实际在独立的 `memory/chekhov.py`（`ChekhovThread`/`ChekhovList`）中，不在 `state.py` 本身；代码库中未找到独立的"骰子历史"字段。

**`memory/database.py`** — SQLite 持久层。替换早期 JSON 文件存储，支持调查员跨 session 复用和存档回溯。

**`combat/orchestrator.py`** — 战斗编排。将原本嵌入 web_server 的 140 行战斗内联代码提取为独立模块，管理遭遇战生成、回合调度和结果判定。

**`adventure/module_composer.py`** — 模块组合引擎。从 110 个 JSON 模块中加权选取，生成包含随机事件和分支路线的完整剧情。支持模块间前置条件、线索链和结局路由。

**`rules/engine.py`** — 通用骰子 + 可插拔解析框架（非 COC 专用）。提供 `roll()`/`roll_damage()` 骰子表达式解析、基于 `SystemProfile` 的通用 `resolve_test()`/`resolve_roll_under()`。**注意**：真正的 COC 7e 规则实现分散在其他文件中——属性检定在 `rules/coc.py`（`resolve_coc`），对抗/伤害计算在 `rules/combat.py`，理智损失在 `rules/sanity.py`，幸运消耗在 `rules/luck.py`。所有骰子都是确定性计算，不依赖 LLM。（2026-07-31 已清理：原文件另携带一大段未使用的 Warhammer Imperium 风格 Psyker/Warp/Perils 机制 + 德语输出文本，代码库中无任何调用方，已删除。）

**`mapgen.py`** — Roguelike 地图生成。基于图形算法为每次跑团生成不同的疗养院地图，包含房间连接图、物品布局和威胁分布。

---

## 数据目录 (`data/`)

### 模块系统 (`modules/`)
110 个独立 JSON 模块，每个包含：
- `module.json` — 场景定义（类型、入口、出口、物品、NPC、战斗遭遇）
- `*.png` — 场景插图（主场景 + 结果画面）

模块类型分布（`module_type`，未显式指定时默认为 `story`）：`story`（剧情，60）、`combat`（战斗，15）、`investigation`（调查，10）、`exploration`（探索，8）、`social`（社交，8）、`horror`（惊悚，5）、`rest`（休整，4）。

### 游戏存档 (`sessions/`)
每个 session 由 `history.jsonl`（对话日志）+ `state.json`（世界状态快照）组成。

### 资源目录
- `characters/Userimage/` — 调查员头像 + 提取头像
- `items/Itemimage/` — 物品图片
- `scenes/Sceneimage/` — 场景背景图
- `bgm/` — 四首场景背景音乐

---

## live 直播模式（commit `56d2b2a`，已合入 v1.0）

- `_vote_window()` 参数化，支持可变超时
- `event_stream()` 和 `/api/stream` 新增 `vote_seconds` 参数
- 模式三分支：`ai` / `human` / `live`
- `_ai_pick_option()` — 超时后 AI 从选项中智能选择
- `LIVE_VOTE_SECONDS = 90` — 直播模式专用投票窗口
- 前端：模式下拉新增"直播"，动态倒计时替代硬编码 30s

详见 `docs/live-mode-plan.md`。

---

## 依赖

```
fastapi, uvicorn, httpx, numpy, Pillow, pyyaml
```

LLM 依赖本地 Ollama 实例（默认 `http://localhost:11434`，可通过 `OLLAMA_HOST` / `OLLAMA_MODEL` 环境变量覆盖，不再硬编码 LAN 主机）。

## 运行

```bash
cd trpg-agent-web
source .venv/bin/activate
python trpg_agent_web/web_server.py --port 8766
# 浏览器打开 http://localhost:8766
```

或通过纯文本 CLI（无 Web 界面，独立入口，不共享 web_server 的直播/投票功能）：

```bash
python -m trpg_agent --adventure 鬼屋
```
