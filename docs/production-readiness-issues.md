# 上线前问题清单（生产就绪度审计）

> 审计日期：2026-07-31。范围：安全性、并发/多用户、健壮性、可观测性、部署、内容合规、前端。
> 格式参考 [known-issues.md](./known-issues.md)：按严重程度分组，修复后在标题追加 ✅ 已修复 并简述方案。

---

## P0 —— 阻断上线，必须先解决

### 1. 硬编码 API Key 已泄露到 git 历史 ✅ 已修复（代码部分）

`scripts/tag_items.py:9`、`scripts/tag_characters.py:9`、`scripts/retag_scenes.py:15` 各硬编码一个 `https://opencode.ai/zen/...` 的真实 `sk-...` API Key。已确认这 3 个文件被 git 跟踪且已提交（`git log` 显示提交 `f0befdf` 起就存在），即密钥已进入版本历史，不只是未提交的本地文件。

**处理方式**：
1. ⚠️ **仍需你手动完成**：立即去服务商后台吊销/轮换该 Key（这一步无法由代码修复替代）。
2. ✅ 已修复：`scripts/tag_items.py`、`scripts/tag_characters.py`、`scripts/retag_scenes.py` 已改为 `API_KEY = os.environ["OPENCODE_API_KEY"]`，不再硬编码；`.env.example` 补充了 `OPENCODE_API_KEY` 占位说明。
3. 按你的选择暂不清理 git 历史（改写历史需单独确认后执行），历史中仍留有旧 Key 明文，务必完成第 1 步轮换。

### 2. 投票 `session_id` 从未真正传到后端——多场对局会串票 ✅ 已修复

- 后端在 `vote` SSE 事件顶层带 `session_id`：[trpg_agent_web/web_server.py](../trpg_agent_web/web_server.py#L806)
- 前端 `vote` 事件监听器只取了 `d.options`，把 `session_id` 丢了：[trpg_agent_web/static/index.html](../trpg_agent_web/static/index.html#L628)
- `showVoteBar` 里 `voteSessionId = opts.session_id || ''` 因此恒为空串：[trpg_agent_web/static/index.html](../trpg_agent_web/static/index.html#L669)
- 每次 `castVote()` → `POST /api/vote` 都带空 `session_id`；后端 [handle_vote()](../trpg_agent_web/web_server.py#L1276) 遇到空值时回退为"投给字典里第一个进行中的场次"

**影响**：只要同时存在 2 场以上直播/对局，所有观众的票会被合并计入最早开始投票的那一场——直播多观众场景下是真实的功能性 bug，不是理论风险。

**修复方案**：前端 `vote` 事件处理改为 `showVoteBar(d)`（把完整事件对象传入，而不是只传 `d.options`），`showVoteBar` 内部同时从顶层读取 `session_id`/`vote_seconds`，`castVote()` 提交时因此带上真实 `session_id`，后端不再需要靠"第一个进行中场次"兜底。

### 3. 无鉴权，也无 CORS 配置 ⏸ 按你的要求暂缓

`/api/stream`、`/api/vote` 没有任何鉴权依赖；FastAPI app 创建处（[web_server.py#L83](../trpg_agent_web/web_server.py#L83)）未注册 `CORSMiddleware`。任何人、任何来源页面都能直接调用这两个接口。

**修复方向**：至少加一层简单 token/session 校验中间件；明确限定允许的跨域来源（如果确实需要跨域）。（本轮修复时你选择暂不处理，需要时再单独排期。）

### 4. LLM API Key 通过 URL 查询参数明文传输 ⏸ 按你的要求暂缓

前端把 `kp_api_key` 拼进 `EventSource` 的 URL：[trpg_agent_web/static/index.html#L455](../trpg_agent_web/static/index.html#L455)。会残留在浏览器历史记录和服务器访问日志中。

**修复方向**：改为服务端持有/配置密钥（环境变量），前端不再经手真实 Key；或至少改用 POST body / 请求头传递。（本轮修复时你选择暂不处理，需要时再单独排期。）

---

## P1 —— 影响健壮性与可运维性，建议上线前完成

### 5. 单进程、纯内存状态，无法横向扩展 ⏸ 未处理

`_vote_tallies`/`_vote_queues` 是模块级全局字典（[web_server.py#L115-116](../trpg_agent_web/web_server.py#L115)），`uvicorn.run(...)` 未设置 `workers`（[web_server.py#L1318](../trpg_agent_web/web_server.py#L1318)）。进程重启会丢失所有进行中的对局/投票状态（仅角色数据落了 sqlite）。需要引入 Redis/共享存储才能真正解决，本轮未处理。

### 6. 无限流/防刷机制 ✅ 已修复

新增 `RateLimitMiddleware`（基于滑动时间窗的按 IP 计数）：每个客户端 IP 在 10 秒窗口内对 `/api/*` 路径最多 20 次请求，超出返回 429。通过 `app.add_middleware(RateLimitMiddleware)` 挂载到 FastAPI app。

### 7. 接口输入校验缺失 ✅ 已修复

`VoteRequest.choice` 改为 `Literal["a", "b", "c"]`，`session_id` 加 `Field(max_length=64)`；`/api/stream` 的 `turns`/`vote_seconds` 用 `Query(ge=..., le=...)` 加范围限制，`seed`/`kp_api_key` 加 `max_length`，`mode` 改为 `Literal["ai", "human", "live"]`。

### 8. 无健康检查、无 metrics/tracing ✅ 部分修复

新增 `GET /health` 端点，返回 `{"status": "ok", "active_sessions": ...}`，供反向代理/容器探活使用。metrics/tracing（Prometheus/OpenTelemetry/Sentry）仍未接入，超出本轮范围。

### 9. 无部署产物 ✅ 已修复

新增仓库根目录 [Dockerfile](../Dockerfile)：基于 `python:3.11-slim`，安装 `.[web,overlay]` 依赖，`EXPOSE 8766`，`HEALTHCHECK` 复用 `/health` 端点，`CMD` 启动 `python -m trpg_agent_web.web_server --host 0.0.0.0 --port 8766`。仍未提供 docker-compose/systemd unit，暂不需要。

### 10. 大量 `except Exception: pass` 静默吞错 ✅ 已修复

`web_server.py` 里原本 7 处静默 `pass` 的 `except Exception`（BGM 映射加载、场景匹配 ×2、TTS、检定解析、AI 玩家流式生成、AI 选项挑选）全部补充了 `log.warning`/`log.debug`（多数带 `exc_info=True`），失败时仍然优雅降级但现在可以在日志里定位问题。

### 11. LLM 返回内容无二次长度上限保护 ✅ 已修复

`_chat_stream()` 新增 `_MAX_LLM_OUTPUT_CHARS = 6000` 硬上限：累计输出达到该长度时记录警告日志、追加"……（内容过长已截断）"提示并提前 `return`，防止模型异常（卡在重复生成/不停止）导致内存无限增长。`_chat_generate()`/`_fake_stream()` 复用同一个入口，自动受益于这个上限。

---

## P2 —— 可后续优化，不阻塞上线

### 12. 前端部分位置用 `innerHTML` 直插文本 ✅ 已修复

`addLog()` 里 `player`/`dice` 分支原先用模板字符串拼接 `innerHTML`，改为 `document.createElement` + `textContent` 逐个构建 `span`/文本节点，不再有未转义的字符串拼进 DOM。物品栏渲染（`inv-*` 元素）里的动态拼接同样改为安全写法。

### 13. 素材版权来源未文档化

`data/scenes`、`data/bgm`、`data/characters/Userimage`、`data/items/Itemimage` 均无 README/LICENSE 说明来源和授权状态。如需公开上线，应确认这些图片/音频素材的版权合规性。

### 14. 前端无构建流程

`trpg_agent_web/static/index.html` 是单文件静态页，无 `package.json`/打包工具。当前规模可接受，持续增长后维护性会下降。

---

## ✅ 已确认没问题的部分（无需处理）

- SQLite 已开启 WAL 模式 + `busy_timeout=5000` + 单连接 `check_same_thread=False`（[trpg_agent/memory/database.py#L107-111](../trpg_agent/memory/database.py#L107)），并发写入保护到位。
- `.env` 已在 `.gitignore` 中排除，`.env.example` 内未包含真实密钥。
