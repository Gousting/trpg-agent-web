# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# 系统依赖：Pillow 处理图片、requests/httpx 走 TLS 需要证书
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY trpg_agent ./trpg_agent
COPY trpg_agent_web ./trpg_agent_web
COPY data ./data

# web = fastapi/uvicorn，overlay = edge-tts（web_server.py 的 TTS 依赖）
RUN pip install --no-cache-dir -e ".[web,overlay]"

ENV PYTHONUNBUFFERED=1
EXPOSE 8766

# 健康检查复用 /health 端点
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "trpg_agent_web.web_server", "--host", "0.0.0.0", "--port", "8766"]
