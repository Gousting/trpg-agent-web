# 前端世界观 UI 配置规范

> 本文档是**长期维护规范**，用于指导后续新增/修改世界观（`world`）时前端界面应如何配置，
> 与 [world-driven-ui-plan.md](./world-driven-ui-plan.md)（一次性设计方案，已实施）配套阅读。
> 代码位置：`trpg_agent_web/static/index.html` 中的 `WORLD_UI_PRESETS` / `applyWorldPreset()`。

## 1. 核心原则：两层初始化

前端任何"随世界观变化"的界面元素，必须归入以下两层之一，不允许再写成散落的
`if (world === 'xxx')` 内联判断：

| 层级 | 触发时机 | 数据来源 | 职责 |
|---|---|---|---|
| **预初始化层** | 用户切换 `#world` 下拉 / 页面加载 | 纯前端静态配置 `WORLD_UI_PRESETS` | 立即给出文案/显隐的"预览"，不依赖后端 |
| **运行时层** | 收到 `init` SSE 事件 | 后端返回的真实数据（如 `d.reincarnator` 是否存在） | 以真实游戏状态为准做最终校正，可覆盖预初始化层的显示 |

**判断新逻辑该放哪层的准则**：如果这个值在用户点"开始跑团"之前就能确定（只取决于
下拉框选了什么），放预初始化层；如果依赖后端返回的具体角色/存档数据，放运行时层。

## 2. `WORLD_UI_PRESETS` 字段规范

新增或修改世界观时，在 `WORLD_UI_PRESETS` 对象里维护一条完整配置，字段固定为：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stageWorldLabel` | string | 舞台顶部世界标签（`#stage-world`），中文短词 |
| `rosterTitle` | string | 左侧角色档案区块标题（`#roster-title`） |
| `voteTitleDefault` | string | 投票条默认标题（`.vote-title`），非 live/无主控场景下显示 |
| `mobileRosterLabel` | string | 移动端悬浮抽屉按钮文案（`#mobile-roster-toggle`） |
| `showLoadProfile` | boolean | 是否显示"继承角色存档"类复选框（`#load-profile-label`） |

**新增字段时**同时做两件事：
1. 在 `WORLD_UI_PRESETS` 每一个世界观条目里都补齐该字段（不允许只写一部分，
   `worldPreset()` 的 `|| WORLD_UI_PRESETS.coc` 兜底只用于未知 world 值，不能替代缺省字段）。
2. 在 `applyWorldPreset()` 里加对应的 DOM 写入逻辑。

禁止在 `WORLD_UI_PRESETS` 之外的地方（如 `syncStageMeta()`、`showVoteBar()`）
再写内联的 `worldValue === 'xxx' ? ... : ...` 三元表达式——一律改为读取
`worldPreset(currentWorld).<field>`。

## 3. 新增一个世界观的 Checklist

1. 后端：确认 `_modules_dir_for_world()`（`trpg_agent_web/web_server.py`）已支持新
   `world` 取值，且 `data/modules_<world>/` 目录存在。
2. 前端 HTML：`<select id="world">` 增加对应 `<option value="...">`。
3. 前端 JS：在 `WORLD_UI_PRESETS` 增加一条完整配置（5 个字段均需填写，参考第 2 节）。
4. 若该世界观有独特的角色卡结构（类似无限流的 `card-reincarnator`），需要在
   `init` SSE 事件监听器里补充"运行时层"的显隐分支，并遵守现有命名规则
   （`card-<角色标识>`，标识可以是姓名或语义化 key）。
5. 若该世界观需要额外的开局选项（类似"继承轮回者"），复用 `showXxx` 布尔字段
   的模式新增一个字段，而不是新建一套独立的显隐机制。
6. 回归测试：在 `tests/test_web_server_regressions.py` 补充字符串级断言，至少覆盖：
   - `WORLD_UI_PRESETS` 中新世界观的条目存在（如 `"<world>: {"` 出现在 HTML 中）；
   - 关键字段的字面值存在（如 `stageWorldLabel: '<期望值>'`）。
7. 手动验证：切换下拉框后，不开局的情况下确认舞台标签/左侧标题/投票默认标题/
   手机按钮文案/继承选项显隐均符合预期；再实际开局一次，确认运行时层不与
   预初始化层冲突。

## 4. 变量与命名约定

- 模块级状态变量统一小写驼峰，且不重复声明局部同名变量：当前只有
  `currentMode`（对应 `#mode` 下拉，`ai`/`human`/`live`）和
  `currentWorld`（对应 `#world` 下拉）两个跨函数共享状态。
  **新增任何需要跨函数读取的下拉框状态时，必须提升为模块级变量，
  不允许在某个函数内用 `const`/`let` 声明后指望其它函数也能访问**
  （这正是本次修复的 `selectedMode` Bug 的成因）。
- 世界观相关 DOM 元素 id 统一使用连字符命名，如 `roster-title`、
  `load-profile-label`、`mobile-roster-toggle`，不使用驼峰或下划线。
- 文案字符串一律使用简体中文（与项目现有 UI 文案保持一致），
  繁体字（如既有的"調查員檔案"）仅在不改动已上线文案时保留，
  新增世界观的文案统一用简体。

## 5. 已知边界（本规范不覆盖）

- 不涉及游戏数值规则层面的差异（如无限流不使用 SAN 值），那属于
  `trpg_agent/memory/game_state.py` 等后端模型的设计范畴。
- 不涉及后端 `/api/stream` 协议本身的字段扩展，只规范前端如何消费/预览。
- 不要求引入前端框架或构建流程，`WORLD_UI_PRESETS` 设计目标是在现有
  单文件原生 JS 结构下保持可维护性，元素数量较少时无需拆分模块。
