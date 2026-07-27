# 社交媒体发帖记录

## 2026-07-23 周三

### 掘金
- **状态**: ✅ 已发布
- **标题**: 用 Ollama 搭了一个能跑 COC 模组的 AI 主持人
- **分类**: 后端
- **标签**: AI
- **内容摘要**: 
  - 为什么做：被 COC 规则劝退，写代码代替读规则
  - 架构：规则引擎/SQLite/上下文管理/地图生成/模组系统
  - 踩坑：AI 调查员信息隔离、检定路由
  - 进度：第一局 22 轮跑通，下一步做直播画面/TTS/BGM
  - 定期更新：每周三六

### 即刻
- **状态**: ✅ 已发布（OpenCLI 即刻 adapter 修复后，CDP Input.insertText 方案）
- **内容**: 搭了一个AI跑团主持人，Ollama本地推理，COC 7e规则引擎，第一局完整跑通了22轮。每周三六更新。#独立开发 #AI跑团
- **备注**: adapter 原用 ClipboardEvent paste → 改为 CDP Input.insertText + execCommand + textContent 三层 fallback

### 小红书
- **状态**: ✅ 已发布（OpenCLI xiaohongshu publish，Z-Image Turbo 生图配图）
- **标题**: AI带团第一周真实体验
- **内容**: 三个踩坑（规则引擎替代读规则、AI信息隔离、KP不会撕卡），22轮跑通，下步搭直播画面+TTS
- **配图**: ComfyUI Z-Image Turbo 生成 COC 暗黑氛围图（1312x1312）

### B站
- **状态**: ⏭️ 跳过（OpenCLI bilibili dynamic 只读不写，手动发待定）
- **备选配图**: 4张 COC 氛围图已生成

---

## 下次发帖计划（周六 7/26）

- 小红书：配 ComfyUI 场景图，短图文格式
- B站动态：配设计稿/地图截图
- 知乎：找 COC 跑团相关问题回答
- 内容方向：本周开发进度（设计稿定版、TTS/BGM 开始搭）
