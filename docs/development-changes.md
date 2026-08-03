# 开发修改记录 (Development Changes Log)

> 每次代码变更后必须追加一条记录：改了什么、改了哪些文件、如何验证。
> 格式：日期 → 变更内容 → 涉及文件 → 验证方式 → 状态。

---

## 2026-08-01 — 多世界观入口：哈利波特模块池

### 变更内容

新增「世界观」选择入口，支持在 COC（默认）与哈利波特两套独立模块池之间切换。核心原则：**不同世界观的模块完全隔离，互不加载、互不影响**。

- 后端新增 `world` 查询参数（`/api/stream`），`event_stream` 据此选择模块池目录
- 新增 `_modules_dir_for_world()` 函数：`coc`（或空）→ `data/modules/`，其他世界观 → `data/modules_<world>/`
- 模块池为空时返回明确错误事件，不静默失败
- 状态事件中标注当前世界观，方便前端/调试识别
- 前端新增「世界观」下拉框（克苏鲁 COC / 哈利波特），透传到 API

### 涉及文件

| 文件 | 改动 |
|---|---|
| `trpg_agent_web/web_server.py` | 新增 `_modules_dir_for_world()`；`event_stream` 与 `/api/stream` 增加 `world` 参数；模块池空校验 |
| `trpg_agent_web/static/index.html` | 控制栏新增「世界观」下拉；`startGame()` 透传 `world` 参数 |
| `data/modules_harry_potter/` | 新增 4 个哈利波特模块（`hogwarts_entrance` / `great_hall_gossip` / `forbidden_corridor` / `troll_encounter`） |

### 新增模块池结构

```
data/modules/                  ← COC 默认（110 模块，未改动）
data/modules_harry_potter/     ← 哈利波特（4 模块，独立）
  ├── hogwarts_entrance/       # 入学之夜（story，起点）
  ├── great_hall_gossip/       # 晚宴流言（social）
  ├── forbidden_corridor/      # 三头犬禁走廊（mystery）
  └── troll_encounter/         # 女盥洗室山怪（combat）
```

### 验证结果

| 验证项 | 结果 |
|---|---|
| `world=harry_potter` 模块加载 | ✅ 4 个模块，19 个场景 |
| `world=coc` 回归 | ✅ 34 个模块，121 个场景（原 110 模块池采样正常） |
| 隔离性 | ✅ 哈利波特池未加载任何 COC 模块，COC 池未加载哈利波特模块 |
| 模块池为空报错 | ✅ 返回明确 error 事件 |

### 状态

✅ 已提交。注：验证时 Ollama 不可达（宿主机服务关闭），模块组合与隔离逻辑已验证，KP 叙事流待 Ollama 恢复后复核。
