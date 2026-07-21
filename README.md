# TRPG Agent Web

网页版 AI 跑团界面。FastAPI + SSE 流式传输 + 程序化地城地图，浏览器打开就能看 AI 跑团。

## 是什么

基于 [trpg-agent](https://github.com/Gousting/trpg-agent) 核心引擎的 Web 前端。KP 和调查员的每一句话通过 SSE 实时流到浏览器，地图在 canvas 上逐格渲染，你可以在网页里观看、插话、回看历史。

## 安装

```bash
pip install trpg-agent-web
```

会自动安装 `trpg-agent[web]` 及其依赖（FastAPI、uvicorn、httpx）。

## 启动

```bash
trpg-web
# 浏览器打开 http://localhost:8766
```

或指定模组：

```bash
trpg-web --adventure 鬼屋
```

## 界面

- **左侧**：程序化地城地图（dungeongen OPD 手绘交叉阴影线风格），带迷雾探索效果
- **右侧**：角色卡 + 对话流 + 检定结果实时展示
- **底部**：输入框，观众可随时插话接管调查员决策

## 技术栈

- FastAPI 后端，SSE 推送游戏事件
- 静态 HTML/CSS/JS 前端，无框架依赖
- Canvas 地图渲染，支持缩放和迷雾
- 与 trpg-agent 核心库共享 Session 状态

## 许可证

MIT
