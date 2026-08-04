# 世界观驱动的前端界面初始化方案

状态：已实施（`WORLD_UI_PRESETS` + `applyWorldPreset()`，见 [trpg_agent_web/static/index.html](../trpg_agent_web/static/index.html)），并附回归测试
关联文件：`trpg_agent_web/static/index.html`

## 1. 背景与问题

当前 `#world` 下拉（COC / 哈利波特 / 无限流）只影响后端模块池选择（`_modules_dir_for_world`）
和极少数展示点，界面语言和区块与所选世界观不一致：

1. 左侧角色面板标题、投票标题等文案在开始游戏前始终是 COC 语义（"調查員檔案"「嫌疑人投票」），
   即使已经选择了"无限流"。
2. 切换发生得偏晚：目前多数世界观相关的界面变化（如轮回者卡片显隐）是在收到后端 `init` SSE
   事件之后才触发，用户在点击"开始跑团"之前看不到与世界观匹配的预览。
3. 手机端悬浮的"调查员"抽屉按钮文案写死，无限流下应显示"轮回者"。
4. 已发现一个潜在 Bug：`showVoteBar()` 内使用了 `selectedMode` 变量，但该变量只在
   `startGame()` 函数作用域内定义，跨函数引用会导致 `ReferenceError`，需要在本次改造中一并修复。

## 2. 设计目标

- 世界观切换后，界面文案与可见区块立即（选择时）做一次预初始化，不必等到开局。
- 服务端 `init` 事件到达后，用实际数据做最终校正（运行时层），保证与后端真实状态一致。
- 三个世界观（COC / 哈利波特 / 无限流）的文案通过一张配置表集中管理，后续新增世界观只需
  加一条配置，不再散落 if/else。
- 顺带修复 `selectedMode` 作用域 Bug。

## 3. 两层初始化模型

### 3.1 预初始化层（用户切换 `#world` 下拉时触发）

用一个 `WORLD_UI_PRESETS` 配置对象驱动，覆盖：

| 元素 | COC | 哈利波特 | 无限流 |
|---|---|---|---|
| 舞台世界标签（`#stage-world`） | COC | 哈利波特 | 无限流 |
| 左侧面板标题（`#roster-title`） | 調查員檔案 | 学生档案 | 輪回者檔案 |
| 投票条默认标题 | 嫌疑人投票 | 抉择投票 | 行动投票 |
| 手机抽屉按钮文案（`#mobile-roster-toggle`） | 调查员 | 学生 | 轮回者 |
| 是否显示"继承轮回者"复选框 | 隐藏 | 隐藏 | 显示 |
| 是否显示 SAN 相关提示 | 显示 | 显示 | 隐藏（无限流不追踪 SAN） |

配置表结构示例：

```js
const WORLD_UI_PRESETS = {
  coc: {
    stageWorldLabel: 'COC',
    rosterTitle: '調查員檔案',
    voteTitleDefault: '嫌疑人投票',
    mobileRosterLabel: '调查员',
    showLoadProfile: false,
  },
  harry_potter: {
    stageWorldLabel: '哈利波特',
    rosterTitle: '学生档案',
    voteTitleDefault: '抉择投票',
    mobileRosterLabel: '学生',
    showLoadProfile: false,
  },
  infinite_flow: {
    stageWorldLabel: '无限流',
    rosterTitle: '輪回者檔案',
    voteTitleDefault: '行动投票',
    mobileRosterLabel: '轮回者',
    showLoadProfile: true,
  },
};
```

新增函数 `applyWorldPreset(worldValue)`：

- 在 `#world` 的 `change` 事件里调用一次（新增监听器）。
- 也在页面加载完成时调用一次（保证初始默认值和下拉框当前值一致）。
- 只负责"预览层"文案与显隐，不改变任何游戏状态变量。

### 3.2 运行时校正层（`init` SSE 事件）

维持现有 `es.addEventListener('init', ...)` 中按 `d.reincarnator` 是否存在切换
`card-reincarnator` / COC 角色卡的逻辑，这一层以后端真实数据为准，预初始化层的文案
在这里可以被二次覆盖（例如断线重连、加载已有 session 等场景）。

约定：预初始化层写的是"即时反馈"，运行时层写的是"权威状态"，两者字段命名保持一致
（`rosterTitle` 等），避免后续再出现文案分叉。

## 4. 需要改动的具体位置

1. `<h2 id="roster-title">調查員檔案</h2>`（已存在 id，无需改结构）。
2. `.vote-title` 默认文本改为由 `applyWorldPreset` 写入，而不是写死在 HTML 里；
   `showVoteBar()` 中"标题决定逻辑"需要按优先级合并：
   - 战斗投票/hub 投票等已有的特殊标题逻辑优先级最高（不变）。
   - 其次是 live 模式下的主控提示。
   - 最后 fallback 到 `WORLD_UI_PRESETS[currentWorld].voteTitleDefault`。
3. `#mobile-roster-toggle` 按钮文案通过 `applyWorldPreset` 写入 `textContent`。
4. `#load-profile` 对应的 `<label>` 整体显隐，通过 `WORLD_UI_PRESETS[..].showLoadProfile`
   控制（非无限流世界观下隐藏，减少无意义的界面噪声）。
5. 修复 Bug：将 `selectedMode` 提升为模块级变量（例如 `let currentMode = 'ai';`），
   在 `startGame()` 内赋值，`showVoteBar()` 等函数改为读取该模块级变量，不再依赖
   函数局部变量的隐式跨作用域访问。

## 5. 实施步骤（建议顺序）

1. 修复 `selectedMode` 作用域 Bug（独立提交，风险最低，先落地）。
2. 引入 `WORLD_UI_PRESETS` 配置对象 + `applyWorldPreset()` 函数。
3. 给 `#world` 绑定 `change` 事件调用 `applyWorldPreset`；页面初始化时调用一次。
4. 调整 `showVoteBar()` 的标题决定逻辑，接入 `voteTitleDefault` 兜底。
5. 调整 `#load-profile` label 与 `#mobile-roster-toggle` 文案的显隐/文案来源。
6. 回归验证：
   - 分别选择三种世界观，观察舞台世界标签、左侧标题、投票默认标题、
     手机抽屉按钮文案是否符合预期表格。
   - 无限流下开局，确认运行时层（`init` 事件）仍能正确显示轮回者卡片，
     预初始化文案不与运行时数据冲突。
   - 直播模式 + 无限流组合下，确认投票标题优先级顺序正确
     （主控提示 > hub 特殊标题 > 世界观默认标题）。

## 6. 非目标（本方案不覆盖）

- 不引入 SAN 的"精简版理智值"替代机制（如需要，见另一份讨论，属于游戏规则层改动，
  不属于本次前端展示层方案范围）。
- 不改变后端 `/api/stream` 参数或事件协议。
- 不新增世界观类型，仅搭建可扩展的配置结构。
