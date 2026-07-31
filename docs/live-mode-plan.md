# Live 模式改造计划

## 目标

新增 `live` 模式，兼容直播和无人场景：始终展示投票选项，观众有互动则跟票走，超时无人投票则 AI 接手决策。

## 改动清单

### 服务端 `trpg_agent_web/web_server.py`

**1. `_vote_window(sid)` → `_vote_window(sid, timeout_seconds)`**

位置：~L328

投票窗口时长参数化，不再硬编码 `VOTE_WINDOW_SECONDS`。

**2. `event_stream()` 签名加参数**

位置：~L355

新增 `vote_seconds: int = 32` 参数。

**3. `init` SSE 事件加字段**

位置：~L447

`"vote_seconds": vote_seconds` 传给前端，用于设置倒计时长度。

**4. 模式三分支**

位置：~L666

```python
if mode == "ai":
    # 现有 AI 模式：LLM 扮演调查员
elif mode == "live":
    # 新：展示选项 + AI fallback
else:
    # 现有 human 模式：展示选项 + 纯等待
```

human 和 live 共用投票选项构建逻辑（~L689-718），区别只在超时处理。

**5. live 模式超时 AI 接手**

位置：~L733-736

```python
if tally:
    choice = max(vote_options.keys(), key=lambda k: tally.get(k, 0))
else:
    if mode == "live":
        # 调 AI player 从 vote_options 中选一个
        choice = await _ai_pick_option(player_host, player_model, vote_options, last_narration)
    else:
        choice = "a"
```

新增 `_ai_pick_option()` 辅助函数：把选项文字喂给本地 Ollama，让它用单字母回复。

**6. live 模式投票窗口用 90 秒**

位置：~L336

```python
timeout = vote_seconds if mode == "live" else VOTE_WINDOW_SECONDS
deadline = loop.time() + timeout
```

**7. `/api/stream` 加 `vote_seconds` 查询参数**

位置：~L1110

### 前端 `trpg_agent_web/static/index.html`

**8. 模式下拉加选项**

位置：模式 `<select>`

```html
<option value="live">直播</option>
```

**9. `startGame()` 读 `vote_seconds`**

位置：~L450

```js
params.append('vote_seconds', document.getElementById('vote-seconds').value || '60');
```

可选：加一个投票秒数输入框，或直接用固定值。

**10. `init` 事件处理读 `vote_seconds`**

位置：~L470

```js
if (d.vote_seconds) totalVoteSeconds = d.vote_seconds;
```

**11. 倒计时用 `totalVoteSeconds` 变量**

位置：前端计时器逻辑

所有硬编码的 30 秒替换为 `totalVoteSeconds`。

### 不需要改动

- 房间威胁、物品拾取、骰子检定——human/live 共用循环后半段
- 战斗检测——`combat_orch.check_combat` 对 human/live 都生效
- AI 模式的自动推进逻辑——保持不变
