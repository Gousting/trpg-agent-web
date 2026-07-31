# 已知问题与修复计划

## 1. 战斗无法自然终结（P0） ✅ 已修复

CombatResolver 代码驱动机制层：从选项文本提取检定类型+难度 → 掷 d100 → 扣血 → 检查结局。LLM 只负责润色叙述。

## 2. 战斗消耗回合预算 ✅ 已修复

`for turn in range(turns)` → `while _non_combat_turns < turns`，战斗回合 `continue` 不消耗预算。

## 3. module_type 标签是死数据 ✅ 已修复

`_transition_hint()` 为 8 种类型切换方向提供差异化叙事指引（调查→战斗、探索→战斗 等）。

## 4. 战斗场景 ID 格式硬编码在两处 ✅ 已修复

`CombatEncounter.combat_scene_id()` / `outcome_scene_id()` 静态方法，统一全仓库引用。

## 5. 战斗结果不回流到叙事状态 ✅ 已修复

`GameState.combat_history` 持久化战斗摘要，`combat_context()` 注入 LLM 系统提示词。

## 6. web_server 战斗逻辑是 140 行内联代码 ✅ 已修复

提取 `CombatOrchestrator`，封装完整战斗生命周期。web_server 战斗块降至 ~70 行。

## 7. 无模块兼容性静态检查

模块要求线索 X 但没有模块产出 X、出口 location_type 全池无匹配——这些错误只在 BFS 运行时静默产生死胡同。

**修复方向**：ModuleComposer 增加 validate() 方法，编译期报告孤岛模块、死线索、无匹配 location_type。

## 8. 战斗难度不看队伍状态 ✅ 已修复

`apply_scaling()` 接受 `party_hp_ratio` 参数，平均 HP < 40% 时敌人 HP 减半。
