"""Boot-time check that the Ollama LLM host is reachable and the model is pulled.

Mirrors ``voice/preflight.py`` and ``__main__._ensure_opus``: a loud, clear message at
startup beats a cryptic ``httpx.ConnectError`` mid-game (which is exactly what happens when
Ollama — its own Windows process — simply isn't running; see docs/conventions.md "LLM not answering?").

This only *checks* and warns. It deliberately does **not** start Ollama: the host may be
remote (the 5080 over Tailscale, ADR 002), and starting a local daemon is the launcher's job
(``start_dmbot.bat``), keeping "Ollama runs as its own process" intact.
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
            "Ollama not reachable at %s (%s) — DM turns will fail. Start Ollama (the Windows "
            "app / `ollama serve`) and enable its autostart; if the host is remote, check the "
            "machine + Tailscale. See docs/conventions.md 'LLM not answering?'.",
            host, exc.__class__.__name__,
        )
        return False

    available = {m.get("name", "") for m in data.get("models", [])}
    if not _model_available(model, available):
        log.warning(
            "Ollama is up at %s but model '%s' is not pulled (have: %s) — DM turns will fail. "
            "Run `ollama pull %s`.",
            host, model, ", ".join(sorted(available)) or "none", model,
        )
        return False

    log.info("Ollama preflight OK — %s reachable, model '%s' available.", host, model)
    return True
