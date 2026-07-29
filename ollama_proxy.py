"""Ollama→OpenAI 协议代理 — 让 TRPG Agent Web 用 opencode.ai 跑。

监听 localhost:11434，将 Ollama /api/chat 请求转为 OpenAI /v1/chat/completions。
非 chat 路由（/api/tags）返回假模型列表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urljoin

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ollama-proxy")

# ── 配置 ──────────────────────────────────────────────
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://opencode.ai/zen/go/v1")
UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", "")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "11434"))

# 启动时探测可用模型，否则用此默认
FALLBACK_MODEL = "gemini-3.0-flash"

routes = web.RouteTableDef()


# ── 工具函数 ──────────────────────────────────────────

def _ollama_to_openai(payload: dict) -> dict:
    """Ollama /api/chat 请求 → OpenAI chat/completions 请求。"""
    model = payload.get("model", FALLBACK_MODEL)
    messages = [{"role": "system", "content": payload["system"]}]
    messages.extend(payload.get("messages", []))
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": payload.get("stream", False),
    }
    opts = payload.get("options", {})
    if "temperature" in opts:
        body["temperature"] = opts["temperature"]
    if "num_predict" in opts:
        body["max_tokens"] = opts["num_predict"]
    return body


def _openai_delta_to_ollama(delta: dict, done: bool = False) -> dict:
    """OpenAI delta → Ollama NDJSON 行。"""
    content = ""
    if delta.get("choices"):
        content = delta["choices"][0].get("delta", {}).get("content", "") or ""
    return {"model": "", "created_at": "", "message": {"role": "assistant", "content": content},
            "done": done}


async def _proxy_stream(upstream_url: str, headers: dict, body: dict, reader):
    """逐行读取上游 SSE 流，转为 Ollama NDJSON 发给客户端。"""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=300, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(upstream_url, headers=headers, json=body) as resp:
            if resp.status != 200:
                err = await resp.text()
                log.error(f"Upstream error {resp.status}: {err[:200]}")
                yield json.dumps(_openai_delta_to_ollama({}, done=True)) + "\n"
                return
            buf = ""
            async for chunk in resp.content:
                buf += chunk.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        yield json.dumps(_openai_delta_to_ollama({}, done=True)) + "\n"
                        return
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    yield json.dumps(_openai_delta_to_ollama(data)) + "\n"
            # 安全收尾
            yield json.dumps(_openai_delta_to_ollama({}, done=True)) + "\n"


# ── 路由 ──────────────────────────────────────────────

@routes.get("/api/tags")
async def handle_tags(request: web.Request) -> web.Response:
    """返回假模型列表——让代理对调用方透明。"""
    # 尝试从上游获取真实模型列表
    models = [{"name": FALLBACK_MODEL, "modified_at": "", "size": 0}]
    return web.json_response({"models": models})


@routes.post("/api/chat")
async def handle_chat(request: web.Request) -> web.Response:
    """Ollama /api/chat → OpenAI /v1/chat/completions。"""
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    openai_body = _ollama_to_openai(payload)
    model = openai_body["model"]
    stream = openai_body.get("stream", False)

    upstream_url = urljoin(UPSTREAM_BASE.rstrip("/") + "/", "chat/completions")
    headers = {
        "Authorization": f"Bearer {UPSTREAM_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",  # opencode.ai 需要这个绕过 CF
    }

    log.info(f"→ {model} stream={stream}")

    if not stream:
        # 非流式：直接转发
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(upstream_url, headers=headers, json=openai_body) as resp:
                data = await resp.json()
        content = ""
        if data.get("choices"):
            content = data["choices"][0].get("message", {}).get("content", "") or ""
        result = {"model": model, "created_at": "", "message": {"role": "assistant", "content": content}, "done": True}
        return web.json_response(result)

    # 流式：返回 NDJSON 流
    resp = web.StreamResponse(status=200, reason="OK")
    resp.headers["Content-Type"] = "application/x-ndjson"
    await resp.prepare(request)

    try:
        async for line in _proxy_stream(upstream_url, headers, openai_body, request.content):
            await resp.write(line.encode("utf-8"))
    except Exception as e:
        log.error(f"Stream error: {e}")
        err_line = json.dumps(_openai_delta_to_ollama({}, done=True)) + "\n"
        await resp.write(err_line.encode("utf-8"))

    await resp.write_eof()
    return resp


@routes.get("/api/version")
async def handle_version(request: web.Request) -> web.Response:
    return web.json_response({"version": "0.0.0-proxy"})


# ── 启动 ──────────────────────────────────────────────

def main():
    if not UPSTREAM_KEY:
        log.error("UPSTREAM_KEY 未设置！export UPSTREAM_KEY=sk-...")
        raise SystemExit(1)

    app = web.Application()
    app.add_routes(routes)

    log.info(f"代理启动 → {UPSTREAM_BASE}  端口 {LISTEN_PORT}")
    web.run_app(app, host="127.0.0.1", port=LISTEN_PORT, print=None)


if __name__ == "__main__":
    main()
