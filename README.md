# TRPG Agent

中文 COC 跑团 AI 主持人基础库。本地 LLM 驱动的克苏鲁的呼唤（Call of Cthulhu 7e）游戏引擎——不是辅助工具，是全自动叙事剧场。

## 是什么

一个用 Python 写的 TRPG 主持人引擎。接上 Ollama，它能自己当 KP（守秘人）、自己控制多个调查员、自己掷骰子判规则、自己生成地图，跑完一整局 COC。你只需要看。

核心设计理念：**自动化叙事剧场**。区别于真人 KP 的"手艺活"路线——AI 主持人驱动多角色自动演绎，观众看故事展开而非看人玩。

## 架构

```
trpg_agent/
├── llm/           # Ollama 客户端、角色人设、prompt 组装、检定路由、输出清洗
├── memory/        # 游戏状态、对话历史、NPC 记忆、上下文压缩、契诃夫之枪追踪
├── rules/         # COC 7e 规则引擎：检定、战斗、理智、孤注一掷、幸运
├── mapgen.py      # 程序化地城地图（dungeongen OPD 交叉阴影线风格）
├── overlay_server.py # OBS 浏览器覆盖层 WebSocket 服务
├── adventure/     # 冒险模组系统、场景变异
├── session.py     # 会话管理器：状态加载/持久化、token 预算、自动存档
└── orchestrator.py # DM 大脑：连接 STT buffer → prompt → LLM → 输出
```

## 安装

```bash
pip install trpg-agent
```

需要本地 Ollama（默认 `localhost:11434`）。

## 快速开始

```bash
# CLI 模式——终端交互式跑团
trpg

# 或先做离线自检（不连接 Ollama）
trpg --check --adventure 鬼屋

# 或执行单轮脚本化验证
trpg --once "陈明检查门厅地板上的拖痕" --adventure 鬼屋

# 或指定模组
trpg --adventure 鬼屋
```

CLI 模式下你会看到一个 KP 和三个调查员在终端里自动跑团，逐轮推进剧情。你随时可以插话接管某个调查员的决策。
默认读取 `OLLAMA_HOST`、`OLLAMA_MODEL`、`OLLAMA_NUM_CTX` 环境变量；未设置时使用 `http://localhost:11434` 和 `qwen2.5:7b`。
如需跳过启动时的 Ollama 连通性检查，可追加 `--skip-preflight`。如需保存终端镜像日志，可设置 `TRPG_LOG_FILE=1`。

## 当前进度

截至 2026-07-27，项目已经完成一轮工程化收口，当前可验证基线如下：

- `python -m pytest -q`：本地通过，**147 项测试全绿**
- `python -m trpg_agent --check --adventure 鬼屋`：本地通过，CLI 启动 / 默认调查员加载 / 模组加载正常
- 默认会话基线已补齐：`data/sessions/default/characters.json` 内置 3 名调查员，可直接用于离线自检
- 文档、安装说明、CLI 参数和测试入口已对齐当前实现，不再依赖早期局域网地址或 Linux-only `uv` 配置

当前最稳定的使用方式是：先跑 `trpg --check --adventure 鬼屋` 确认本地基线，再接入本机 Ollama 做实机单轮验证。

## 可选依赖

```bash
# Web 界面（FastAPI + SSE + 地图渲染）
pip install trpg-agent[web]

# 语音识别（faster-whisper）
pip install trpg-agent[voice]

# OBS 浏览器覆盖层（aiohttp + WebSocket）
pip install trpg-agent[overlay]

# 测试
pip install trpg-agent[test]
```

### OBS 直播覆盖层

哥特恐怖风浏览器覆盖层，通过 WebSocket 实时推送游戏状态到 OBS：

```bash
python -m trpg_agent.overlay_server
# 端口 8766，OBS 浏览器源 URL: http://localhost:8766/
```

REST API 控制：`POST /api/scene`（切换场景卡）、`/api/roll`（掷骰动画）、`/api/characters`（角色卡）、`/api/push_line`（旁白推送）、`/api/danmaku`（弹幕）、`/api/vote`（观众投票）。

场景卡使用 ComfyUI Z-Image Turbo + Ink Frenzy 风格批量预生成，16:9 宽幅、暗黑克苏鲁氛围。参见 `skills/coc-scene-card-generator`。

## 当前状态

**可用的：** COC 规则引擎完整覆盖 7e 核心机制（检定/战斗/理智/孤注一掷/幸运）、Ollama 本地推理、SQLite 持久化（调查员跨 session 复用）、程序化地城地图、多智能体协作（KP + 3 玩家自动跑）、上下文窗口管理（token 预算 + recap 压缩）、场景卡模组系统、**OBS 浏览器覆盖层（WebSocket 实时推送 + REST API + 哥特恐怖风 UI）**、**overlay 旁白 TTS 自动生成（`/api/push_line` + edge-tts + 缓存）**、**场景 mood → BGM 自动切换（`/api/scene_match` / `/api/bgm` + `bgm_mappings.json`）**、**ComfyUI Z-Image 场景卡批量预生成**、**CLI 离线自检与本地测试基线**。

**开发中：** 弹幕互动桥接、真实 Ollama 联机回合的持续冒烟验证，以及 TTS/BGM 与主叙事流程的更深度自动联动。

## 依赖

- Python ≥ 3.11
- [Ollama](https://ollama.com)（本地 LLM 推理）
- dungeongen（地图渲染）
- Rich（终端美化）

## 许可证

MIT
