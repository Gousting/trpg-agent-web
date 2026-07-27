"""Boot-time check that the Ollama host is reachable and the selected model is available.

A clear startup warning beats a cryptic ``httpx.ConnectError`` in the first turn. The check is
non-fatal by design: it warns early, but still lets the CLI come up for offline inspection.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


def _model_available(model: str, available: set[str]) -> bool:
    """Is ``model`` among the tags Ollama reports? Matches with or without the ``:latest``
    tag (``ollama list`` reports ``mistral-nemo:latest``; the config default is ``mistral-nemo``).
    A pure helper so the matching is unit-testable without a live daemon."""
    if model in available:
        return True
    base = {name.split(":", 1)[0] for name in available}
    return model.split(":", 1)[0] in base


def check_ollama(host: str, model: str, *, timeout: float = 5.0) -> bool:
    """Ping the Ollama host and verify the model is pulled. Returns True if all good.

    Never raises — a preflight must not break boot; on any problem it logs a clear,
    actionable message and returns False so the bot still starts (the turn will fail loudly
    later, but at least the operator saw the reason at startup)."""
    host = host.rstrip("/")
    try:
        resp = httpx.get(f"{host}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — any failure means "not usable", report it
        log.error(
            "Ollama not reachable at %s (%s) — KP 回合将失败。请启动 Ollama（桌面应用或 `ollama serve`），"
            "并检查 OLLAMA_HOST / --host 是否正确。",
            host, exc.__class__.__name__,
        )
        return False

    available = {m.get("name", "") for m in data.get("models", [])}
    if not _model_available(model, available):
        log.warning(
            "Ollama 已连接到 %s，但模型 '%s' 未拉取（当前有: %s）— KP 回合将失败。请执行 `ollama pull %s`。",
            host, model, ", ".join(sorted(available)) or "none", model,
        )
        return False

    log.info("Ollama 预检通过：%s 可访问，模型 '%s' 可用。", host, model)
    return True
