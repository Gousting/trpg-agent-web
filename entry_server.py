"""快捷入口服务 — 独立端口运行，根路径返回 entry.html，其余请求挂载主应用。

用途：主播/观众通过公网访问 8767 时直接看到「开始游戏」快捷入口页，
KP 与 NPC 配置固定为 opencode deepseek-v4-flash，只需填入 API Key。
所有 API/静态资源（/api/stream、/images、/audio）由主应用处理。

启动：
    uv run uvicorn entry_server:app --host 0.0.0.0 --port 8767
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from trpg_agent_web.web_server import app as web_app

STATIC_DIR = Path(__file__).parent / "trpg_agent_web" / "static"
ENTRY_HTML = STATIC_DIR / "entry.html"

app = FastAPI(title="TRPG 快捷入口")


@app.get("/", include_in_schema=False)
async def entry_page():
    # no-store：禁止浏览器/Cloudflare 缓存入口页，确保每次拿到最新版本
    return FileResponse(ENTRY_HTML, headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})


@app.get("/index.html", include_in_schema=False)
async def index_page():
    # 快捷入口打开的游戏界面（主应用只有 "/" 路由，8767 的 "/" 被入口页占用）
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})


# 其余路径（API / 静态资源 / SSE 流）全部交给主应用
app.mount("/", web_app)
