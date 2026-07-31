# TRPG Agent Web — 架构文档

> 更新：2026-07-31 | 基于 `76121a2` + live-mode 未提交改动

## 项目概览

本地优先的克苏鲁 TRPG AI 跑团系统。KP（守秘人）和调查员均由本地 Ollama 模型扮演，Web 前端通过 SSE 实时推送叙事流，支持 AI 自动 / 人类玩家 / 直播三种模式，34 个可组合剧情模块驱动沙盒叙事。

- **语言**：Python 3.11
- **LLM**：Ollama (ornith:9b / gemma4:12b / qwen3.6-27b)
- **Web**：FastAPI + SSE + 纯 HTML/JS 前端
- **存储**：SQLite + JSON 文件
- **依赖管理**：uv + pyproject.toml

---

## 目录结构

```
trpg-agent-web/
├── trpg_agent_web/             # Web 层
│   ├── web_server.py           # ★ FastAPI 入口 + SSE 游戏循环 (1222行)
│   └── static/
│       └── index.html          # ★ 前端 SPA (880行)
│
├── trpg_agent/                 # 核心引擎
│   ├── orchestrator.py         # ★ 游戏回合编排 (1017行)
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
│   │   ├── module_composer.py  # ★ 模块组合引擎 (1284行)
│   │   └── variance.py         # 随机变量注入
│   │
│   ├── rules/                  # COC 规则引擎
│   │   ├── engine.py           # ★ 骰子+检定核心 (427行)
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
│   ├── shutdown.py             # 优雅关闭
│   ├── logsetup.py             # 日志配置
│   ├── rag/                    # RAG 检索
│   ├── tools/                  # 工具调用
│   ├── tts/                    # 文字转语音
│   └── __main__.py             # CLI 入口
│
├── data/
│   ├── modules/                # 34 个 JSON 剧情模块
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
├── tests/                      # 测试 (171 pass / 8 fail)
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
                    │   SSE 生成器         │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
     Session (session.py)  Orchestrator    规则引擎
     - 状态加载/保存       - 回合推进      - 骰子检定
     - 对话历史            - 模式分发      - 技能判定
     - NPC 记忆            - KP 叙事       - 伤害计算
                           - 玩家行动
```

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

**`orchestrator.py`** — 回合编排器。将玩家输入（AI 生成或人类投票）转化为 KP 回答。管理叙事管道：前置校验 → prompt 组装 → LLM 调用 → 输出清洗 → 状态更新。

**`session.py`** — 生命周期管理。加载/保存/恢复游戏会话，连接记忆、状态和历史，为每回合提供完整上下文。

**`llm/client.py`** — Ollama HTTP 客户端包装。`OllamaClient` 类提供 `chat()` 方法，支持流式输出和独立上下文窗口。

**`llm/prompt_assembly.py`** — 将世界状态、对话历史、角色人设、规则参考组装成每次 LLM 调用的 prompt。

**`memory/state.py`** — "硬事实"存储。调查员属性（HP/SAN/技能/物品）、场景状态、骰子历史、契诃夫之枪追踪。所有可被规则引擎查询的数据。

**`memory/database.py`** — SQLite 持久层。替换早期 JSON 文件存储，支持调查员跨 session 复用和存档回溯。

**`combat/orchestrator.py`** — 战斗编排。将原本嵌入 web_server 的 140 行战斗内联代码提取为独立模块，管理遭遇战生成、回合调度和结果判定。

**`adventure/module_composer.py`** — 模块组合引擎。从 34 个 JSON 模块中加权选取，生成包含随机事件和分支路线的完整剧情。支持模块间前置条件、线索链和结局路由。

**`rules/engine.py`** — 核心骰子引擎。COC 7e 规则的完整实现：属性检定、对抗检定、伤害计算、理智损失、幸运消耗。确定性计算，不依赖 LLM。

**`mapgen.py`** — Roguelike 地图生成。基于图形算法为每次跑团生成不同的疗养院地图，包含房间连接图、物品布局和威胁分布。

---

## 数据目录 (`data/`)

### 模块系统 (`modules/`)
34 个独立 JSON 模块，每个包含：
- `module.json` — 场景定义（类型、入口、出口、物品、NPC、战斗遭遇）
- `*.png` — 场景插图（主场景 + 结果画面）

模块类型：`exploration`（探索）、`combat`（战斗）、`story`（剧情）、`investigation`（调查）、`social`（社交）、`ritual`（仪式）、`escape`（逃脱）

### 游戏存档 (`sessions/`)
每个 session 由 `history.jsonl`（对话日志）+ `state.json`（世界状态快照）组成。

### 资源目录
- `characters/Userimage/` — 调查员头像 + 提取头像
- `items/Itemimage/` — 物品图片
- `scenes/Sceneimage/` — 场景背景图
- `bgm/` — 四首场景背景音乐

---

## 未提交改动 (live-mode)

基于 `76121a2`，在 `web_server.py` 和 `index.html` 中共 49 行改动：

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

LLM 依赖本地 Ollama 实例（默认 `192.168.0.105:11434`）。

## 运行

```bash
cd trpg-agent-web
source .venv/bin/activate
python trpg_agent_web/web_server.py --port 8766
# 浏览器打开 http://localhost:8766
```

或通过 CLI：

```bash
python -m trpg_agent --web --port 8766
```
