# TRPG Agent — 中文 COC 跑团 KP

本地 AI 主持人，跑《克苏鲁的呼唤》。你说，它听，然后以守秘人的身份回答——全部本地运行，零 API 成本。

> **状态：Phase 1 完成** — 纯文字闭环跑通。gemma4:12b 实测三轮对话，输出合格的 KP 叙述。

## 怎么跑

```bash
git clone https://github.com/Gousting/trpg-agent.git
cd trpg-agent
uv sync
cp .env.example .env   # 编辑 OLLAMA_HOST 指向你的 Ollama
uv run python tests/test_pipeline.py
```

前提：Ollama 运行中，已 pull 模型（默认 gemma4:12b）。

```bash
# 单元测试
uv run pytest tests/test_unit.py -v
```

## 架构

借鉴 [DMbot](https://github.com/Pr0degie/dungeonmaster) 的架构范式——"LLM 提议叙事，代码拥有硬状态"。

```
玩家输入 → [Ollama] → 中文 KP 回答 → sanitize 清洗 → 输出
                ↑
         system prompt（KP 人格 + 世界状态 + 前情提要）
```

三层核心：

- **`prompts/kp_core_zh.md`** — 守秘人核心人格，52 行 2587 字。涵盖回应规则、恐怖氛围、NPC 处理、游戏规则
- **`llm/sanitize.py`** — KP 回答清洗管道，12 项正则模式。去掉元话语、角色标签、AI 自指、过渡词
- **`rules/coc.py`** — COC 7 版检定引擎，常规/困难/极难三级难度 + 大成功/大失败判定

## 文件状态

| 模块 | 状态 | 说明 |
|------|------|------|
| `llm/sanitize.py` | ✅ | 中文 KP 回答清洗 |
| `prompts/kp_core_zh.md` | ✅ | 守秘人核心人格 |
| `rules/coc.py` | ✅ | COC 检定引擎 |
| `rules/engine.py` | ✅ | 通用掷骰引擎 |
| `llm/client.py` | ✅ | Ollama 客户端 |
| `llm/persona.py` | ✅ | Prompt 加载 |
| `llm/prompt_assembly.py` | ✅ | Prompt 组装 |
| `tests/test_unit.py` | ✅ | 39 项单元测试 |
| `tests/test_pipeline.py` | ✅ | 全链路集成测试 |
| 其他 24 个文件 | 🔧 | 搬运自 DMbot，待中文化 |

## 许可

MIT
