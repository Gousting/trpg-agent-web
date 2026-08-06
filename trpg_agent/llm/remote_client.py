"""OpenAI-compatible remote chat client.

Thin async wrapper for any OpenAI-compatible API (openrouter, opencode, etc.).
Exposes the same .chat() / .chat_stream() interface as OllamaClient so
web_server can swap between local and remote without fork.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

log = logging.getLogger(__name__)


class RemoteClient:
    """Minimal async OpenAI-compatible client."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self.last_stats: dict | None = None

    @property
    def model(self) -> str:
        return self._model

    async def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        options: dict | None = None,
        format: dict | str | None = None,
    ) -> str:
        """Non-streaming chat. format (json schema or "json") → response_format."""
        opts = dict(options or {})
        payload: dict = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": opts.get("temperature", 0.8),
            "max_tokens": opts.get("num_predict", 2000),
        }
        if isinstance(format, dict):
            payload["response_format"] = {"type": "json_schema", "json_schema": format}
        elif format == "json":
            payload["response_format"] = {"type": "json_object"}

        resp = await self._client.post(
            f"{self._base_url}/chat/completions", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        self.last_stats = {
            "prompt_eval_count": data.get("usage", {}).get("prompt_tokens"),
            "eval_count": data.get("usage", {}).get("completion_tokens"),
        }
        return (choice["message"]["content"] or "").strip()

    async def chat_stream(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        """Streaming chat — yields text deltas."""
        opts = dict(options or {})
        payload = {
            "model": self._model,
            "stream": True,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": opts.get("temperature", 0.8),
            "max_tokens": opts.get("num_predict", 2000),
        }

        async with self._client.stream(
            "POST", f"{self._base_url}/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            finish_reason = None
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except ValueError:
                    continue
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish_reason = fr
            self.last_stats = {"finish_reason": finish_reason}

    async def aclose(self) -> None:
        await self._client.aclose()
