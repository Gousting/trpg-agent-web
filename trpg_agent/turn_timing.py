"""Per-DM-turn latency record + the context-budget warning threshold.

Pulled out of :mod:`dmbot.runtime` for context-leanness (ADR 034's pattern): ``_TurnTiming`` is a
self-contained, state-free logging helper (it threads ``time.monotonic`` timestamps through the turn
flow and emits one ``[latency]`` line per turn — no ``SessionRuntime`` state), so an agent editing
the latency/ctx logging no longer pulls the whole session runtime into context. ``runtime`` re-exports
both names, so ``from ..runtime import _TurnTiming`` keeps working unchanged.

Docs and code are English; game content (what the DM says) stays German (CLAUDE.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Context-budget smoke signal: warn once a narration prompt fills more than this fraction of
# num_ctx. Above it, Ollama starts truncating the prompt *head* — which is the persona (the worst
# part to silently lose), since the system prompt leads. The grower is the 20-turn history + the
# recap + the state block, so the fix is to trim those, not raise the cap (KV-cache VRAM).
_CTX_WARN_FRACTION = 0.85


@dataclass
class _TurnTiming:
    """Per-DM-turn latency record (logging only — no behaviour change, no ADR). Timestamps are
    ``time.monotonic`` carried through the existing turn flow (trigger → respond → speak); the
    deltas are emitted as one ``[latency]`` line per turn at the end of ``_deliver_answer``.

    Stages: stt (last routed utterance's transcribe ms), trigger→llm_done (turn start → Ollama
    returned, with the autosend ``wait_idle`` portion broken out), tts (synth → WAV), bridge_wait
    (``/speak`` POST → return), total (trigger → ``/speak`` returned). ctx/gen come from Ollama.
    """

    turn: int
    trigger: float  # monotonic at turn start (the trigger fired)
    kind: str = ""  # "", "redo", "auto", "roll" — which trigger started the turn
    streamed: bool = False  # True when the turn used the streaming pipeline (ADR 017)
    wait_ms: int = 0  # wait_idle before respond (autosend only); 0 otherwise
    stt_ms: int | None = None  # transcribe ms of the last DM-routed utterance (None if not speech-driven)
    llm_done: float | None = None  # monotonic when Ollama returned (generation finished)
    first_audio: float | None = None  # monotonic at the first /speak POST (streaming time-to-first-audio)
    prompt_eval: int | None = None  # Ollama prompt_eval_count (context tokens)
    eval_count: int | None = None  # Ollama eval_count (generated tokens)
    num_ctx: int | None = None  # the num_ctx cap in effect
    answer_chars: int = 0
    tts_ms: int | None = None  # synth call → WAV ready (streaming: summed over sentences)
    wav_s: float | None = None  # WAV duration (streaming: summed); contextualises tts/bridge_wait
    bridge_ms: int | None = None  # /speak POST → return = playback + transfer (streaming: summed)
    end: float | None = None  # monotonic when the last /speak returned

    def take_llm_stats(self, stats: dict | None) -> None:
        if not stats:
            return
        self.prompt_eval = stats.get("prompt_eval_count")
        self.eval_count = stats.get("eval_count")
        self.num_ctx = stats.get("num_ctx")

    def respond_ms(self) -> int:
        """The pure LLM-generation time (trigger→llm_done minus the wait_idle portion) — i.e. the
        meaning of the existing ``⏱ LLM`` log line, preserved across the four trigger sites."""
        if self.llm_done is None:
            return 0
        return round((self.llm_done - self.trigger) * 1000) - self.wait_ms

    def ctx_over_budget(self, fraction: float = _CTX_WARN_FRACTION) -> bool:
        """True when this turn's prompt filled more than ``fraction`` of num_ctx — the early signal
        (before Ollama truncates the prompt head) that the growing system prompt needs trimming."""
        return (
            self.prompt_eval is not None
            and bool(self.num_ctx)
            and self.prompt_eval > fraction * self.num_ctx
        )

    def log_line(self) -> None:
        """Emit the one compact ``[latency]`` line for this turn (INFO → console + debug.log)."""
        def ms(v: int | None) -> str:
            return f"{v}ms" if v is not None else "—"

        parts = [f"turn={self.turn}"]
        if self.kind:
            parts.append(self.kind)
        if self.streamed:
            parts.append("stream")
        parts.append(f"stt={ms(self.stt_ms)}")
        if self.wait_ms:
            parts.append(f"wait={self.wait_ms}ms")
        t2l = round((self.llm_done - self.trigger) * 1000) if self.llm_done is not None else None
        parts.append(f"trigger→llm_done={ms(t2l)}")
        if self.prompt_eval is not None:
            parts.append(f"ctx={self.prompt_eval}/{self.num_ctx}" if self.num_ctx
                         else f"ctx={self.prompt_eval}")
        if self.eval_count is not None:
            parts.append(f"gen={self.eval_count}")
        parts.append(f"chars={self.answer_chars}")
        # Headline metric for streaming (ADR 017): trigger → first audio leaves Bot A.
        if self.first_audio is not None:
            parts.append(f"first_audio={round((self.first_audio - self.trigger) * 1000)}ms")
        parts.append(f"tts={ms(self.tts_ms)}")
        if self.wav_s is not None:
            parts.append(f"wav={self.wav_s:.1f}s")
        parts.append(f"bridge_wait={ms(self.bridge_ms)}")
        total = round((self.end - self.trigger) * 1000) if self.end is not None else None
        parts.append(f"total={ms(total)}")
        log.info("[latency] %s", " ".join(parts))
        # Context-budget early warning (narration turns only — those are the ones that build a
        # _TurnTiming). Above ~85% of num_ctx, Ollama truncates the prompt head (the persona) first.
        if self.ctx_over_budget():
            log.warning(
                "[ctx] prompt %d/%d tokens (>%d%% of num_ctx) — nearing the cap; Ollama will start "
                "truncating the prompt head (persona first). Trim history / recap / state block.",
                self.prompt_eval, self.num_ctx, round(_CTX_WARN_FRACTION * 100),
            )
