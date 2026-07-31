# 已知问题与修复计划

## 1. 战斗无法自然终结（P0）

CombatLoop 判定结局只靠 `all_enemies_dead()`，但 LLM 叙述"长老轰然倒地"后没有任何代码调用 `enemy.take_damage()`。纯文本输出不转化为状态变更。

**修复方向**：创建 CombatResolver，在 submit_vote → resolve 之间插入代码驱动的机制层：
- 解析选项中的检定类型（STR/DEX/CON/...）和难度
- 掷骰判定成功/失败
- 应用伤害/状态效果到敌人和调查员
- 产出结构化结算结果 → LLM 只负责润色叙述

## 2. 战斗消耗回合预算

每轮战斗占用 `for turn in range(turns)` 的一个 slot，一场 4 轮战斗吃掉近半进度。叙事密度不均衡。

## 3. module_type 标签是死数据

六种非 combat 类型在引擎层面行为完全一致，仅写入 transition 元数据但从未被消费。给模块作者造成"填了就有差异化行为"的假象。

**修复方向**：web_server 读取 transition 元数据，在类型切换时生成差异化的 KP 过渡指令（如调查→战斗时强调氛围变化）。

## 4. 战斗场景 ID 格式硬编码在两处

`{module_id}::combat_{outcome_id}` 在 module_composer.py 和 web_server.py 各硬编码一次。改了生成规则会静默跳转失败。

**修复方向**：提取为 CombatEncounter 或 ModuleComposer 上的常量/方法。

## 5. 战斗结果不回流到叙事状态

outcomes.provides_clues 传给下游做入口匹配，但战斗过程（残血、疯狂、轮数）全部丢弃。惨胜 vs 碾压对后续故事影响完全一样。

**修复方向**：CombatLoop 结束时分出 combat_summary 结构存入 session，后续 LLM prompt 可引用。

## 6. web_server 战斗逻辑是 140 行内联代码

event_stream 超过 700 行，战斗块的 SSE 推送、投票、跳转全手写。无法单独测试、无法复用。

**修复方向**：提取 CombatOrchestrator，封装"检测战斗场景 → 初始化循环 → LLM 生成 → 投票 → 结算 → 跳转"完整流程。

## 7. 无模块兼容性静态检查

模块要求线索 X 但没有模块产出 X、出口 location_type 全池无匹配——这些错误只在 BFS 运行时静默产生死胡同。

**修复方向**：ModuleComposer 增加 validate() 方法，编译期报告孤岛模块、死线索、无匹配 location_type。

## 8. 战斗难度不看队伍状态

apply_scaling() 只按人数缩放，不管调查员进场时是满血还是残血。

**修复方向**：apply_scaling() 接受 investigators_state 参数，根据平均 HP/SAN 调整敌人强度。
