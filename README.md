# TRPG Agent — 中文 COC 跑团 KP

本地 AI 主持人，跑《克苏鲁的呼唤》。你说，它听，然后以守秘人的身份回答——全部本地运行，零 API 成本。

> **状态：Phase 4 完成** — SAN/战斗/幸运/孤注一掷/recap 压缩/NPC 记忆全部就绪。6 轮全链路集成测试通过。

## 怎么跑

```bash
git clone https://github.com/Gousting/trpg-agent.git
cd trpg-agent
uv sync
cp .env.example .env   # 编辑 OLLAMA_HOST 指向你的 Ollama
uv run python tests/test_integration.py   # Phase 4 全链路测试 (6 轮)
```

前提：Ollama 运行中，已 pull 模型（默认 gemma4:12b）。

```bash
uv run pytest tests/ -v   # 59 项单元测试
```

## 架构

借鉴 [DMbot](https://github.com/Pr0degie/dungeonmaster) 的架构范式——"LLM 提议叙事，代码拥有硬状态"。

```
玩家输入 → 检定分类器 → 掷骰引擎 → [Ollama] → sanitize 清洗 → KP 回答
                ↓                        ↑
         SAN/战斗/幸运/孤注一掷    system prompt（人格 + 状态 + NPC记忆 + 前情提要）
                ↓                        ↑
         Session 管理器（角色卡 + 对话历史 + 上下文窗口 + recap压缩）
```

## 已完成模块

**Phase 1-2：核心管线**
| 模块 | 说明 |
|------|------|
| `llm/sanitize.py` | 中文 KP 回答清洗 (12 项正则) |
| `llm/client.py` | Ollama 异步客户端 |
| `prompts/kp_core_zh.md` | 守秘人核心人格 (2587 字) |
| `rules/coc.py` | COC 7 版检定引擎 |
| `rules/engine.py` | 通用掷骰引擎 |
| `session.py` | Session 管理器 |
| `memory/game_state.py` | COC 游戏状态 |
| `memory/history.py` | 对话历史 |

**Phase 3：掷骰路由**
| 模块 | 说明 |
|------|------|
| `llm/roll_router.py` | 检定分类器 (constrained JSON) |

**Phase 4：完整游戏系统**
| 模块 | 说明 |
|------|------|
| `rules/sanity.py` | SAN 检定、临时/不定疯狂 |
| `rules/combat.py` | 格斗/射击/闪避/反击 |
| `rules/luck.py` | 幸运值消耗与恢复 |
| `rules/pushing.py` | 孤注一掷重试 |

**测试**
| 文件 | 说明 |
|------|------|
| `tests/test_unit.py` | 39 项单元测试 |
| `tests/test_phase4.py` | 20 项规则测试 |
| `tests/test_integration.py` | 6 轮全链路集成测试 |

## 迭代路线

详见 [ROADMAP.md](ROADMAP.md)。
Phase 1 纯文字闭环 ✅ · Phase 2 状态与记忆 ✅ · Phase 3 掷骰路由 ✅ · Phase 4 完整游戏系统 ✅ · Phase 5 语音链路 → Phase 6 平板客户端。

## 许可

MIT
