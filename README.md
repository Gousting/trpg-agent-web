# TRPG Agent Web

网页版 AI 跑团直播界面。FastAPI + SSE 流式传输 + 程序化地城地图 + TTS 语音合成，OBS 浏览器源直接采集即可开播。

## 是什么

基于 [trpg-agent](https://github.com/Gousting/trpg-agent) 核心引擎的 Web 直播前端。KP 和调查员的每一句话通过 SSE 实时流到浏览器，地图在 canvas 上逐格渲染，场景背景根据氛围自动切换，旁白由 TTS 语音合成朗读。观众可以在网页里观看、插话、投票、回看历史。

## Phase 1 功能（已完成）

| 模块 | 说明 |
|------|------|
| 🗺️ 程序化地图 | dungeon-gen OPD 手绘风格，迷雾探索效果，房间切换过渡动画 |
| 🎬 场景背景 | 38 张氛围图，根据 KP 描述语义匹配自动切换（setTimeout 兜底激活） |
| 🎵 BGM | 4 轨合成音，mood → track 映射，交叉淡变过渡 |
| 👤 角色卡 | 11 组 OpenCV 人脸裁切头像，HP/SAN/LUCK 三属性条 |
| 🎲 骰子检定 | 浮动祭坛 + 骰子面动画 + 判定结果实时展示 |
| 🔊 TTS 旁白 | edge-tts 自动合成（zh-CN-YunyangNeural），文本 MD5 缓存 |
| 🏆 物品弹出 | 15 道具，6s 弹出动画 |
| 💬 弹幕/投票 | UI 就绪，B站 WebSocket 接入 |
| 📱 布局适配 | 1920×1080 三区域（地图/对话/角色卡），深色 COC 主题 |

## 安装

```bash
pip install trpg-agent-web
```

会自动安装 `trpg-agent[web]` 及其依赖（FastAPI、uvicorn、httpx、edge-tts）。

## 启动

```bash
trpg-web
# 浏览器打开 http://localhost:8766
```

或指定模组：

```bash
trpg-web --adventure 鬼屋
```

## OBS 直播配置

1. OBS 添加「浏览器」源
2. URL 填 `http://localhost:8766`
3. 分辨率设为 1920×1080，FPS 30
4. 点击页面「▶ 开始直播」解锁 Chrome 音频限制
5. 全链路自动运行：场景切换 → BGM 播放 → TTS 旁白 → 骰子检定 → 物品弹出

## 技术栈

- FastAPI 后端，SSE 推送游戏事件
- 静态 HTML/CSS/JS 前端，无框架依赖
- Canvas 地图渲染，支持缩放和迷雾
- edge-tts 语音合成，MD5 缓存
- 与 trpg-agent 核心库共享 Session 状态

## 许可证

MIT
