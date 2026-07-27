# 模块化剧情系统 — B 方案：模板化生成

**状态：规划中，尚未实施。** 记录于 2026-07-13，A 方案（多出口分支）已实现。

## 核心思路

模块作者不写死具体场景描述，只定义结构骨架——这个场景要揭示什么线索、触发什么检定、由哪些叙事节拍组成。实际的地点描述、NPC 名字、氛围细节由 LLM 在运行时根据上下文填充。

## 模块骨架格式

```json
{
  "id": "research_scene",
  "title_template": "{investigator}在{location}的发现",
  "genre": ["investigation"],
  "difficulty": 2,
  "slots": {
    "location": {"type": "indoor", "pool": ["图书室", "实验室", "档案室", "废弃教堂"]},
    "mood": {"pool": ["阴暗", "压抑", "诡异", "寂静"]},
    "npc_role": {"pool": ["守秘人", "目击者", "背叛者", "疯子"]}
  },
  "beats": [
    {"type": "description", "template": "{location}里{mood_detail}。{npc_name}坐在角落，{npc_action}。"},
    {"type": "clue", "id": "main_clue", "template": "翻阅{clue_source}（{skill_check}）——{clue_content}"},
    {"type": "secret", "id": "secret1", "template": "{npc_name}的真正身份是{npc_role}。{secret_detail}"},
    {"type": "exit", "condition": "main_clue resolved", "leads_to": "next_module"}
  ],
  "exit": {
    "provides_clues": ["{module_id}_clue_found"],
    "mood": "{mood}",
    "next_location_type": "any"
  }
}
```

## 填充流程

1. 组合引擎选定模块序列后，收集上下文（前置模块的地点、情绪、已出现 NPC）
2. 将模块骨架 + 上下文送入 LLM，LLM 填充所有 `{slot}` 占位符
3. 产出完整 `module.json`（与 A 方案同构），走现有管道

## 与 A 方案的关系

- A 方案：手写模块，确定性高，适合精心设计的剧情线
- B 方案：模板生成，产出效率高一个数量级，适合填充模块池的"肉"
- 两者共享同一套出口/入口兼容性协议，可以在同一次组合中混用——手写模块定义关键转折点，模板模块填充过渡和支线

## 待解决问题

- 上下文窗口：一次 LLM 调用填充多少模块？全链填充 vs 逐模块填充
- 一致性：LLM 填充的 NPC 可能前后矛盾（同一个名字两次出现变成不同人）
- 质量控制：模板模块的质量方差可能很大，需要设计自动审查流程

## 实施优先级

低。先用手写模块建立基准质量线，确认组合引擎 + 游戏体验达标后，再引入模板化来扩大内容规模。
