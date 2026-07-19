"""Step-wise shutdown progress (console) + abandonable worker threads.

Why this exists: Ctrl+C used to paint a bare "Shutting down..." dots line, so the operator
could not tell *what* was being torn down, how many steps remained, or which one was slow.
Worse, TTS synthesis ran via ``asyncio.to_thread`` on asyncio's default executor, whose
threads are non-daemon — the interpreter joins them at exit, so a mid-sentence GPU synth
(near-permanent while streaming, ADR 017) held Ctrl+C hostage for its full remaining time.

Two pieces:

- ``progress`` — a singleton :class:`ShutdownProgress`. ``DMBot.close()`` declares the total
  step count, every teardown stage wraps itself in ``progress.step("label")``, and the console
  shows ``[i/n] label ...`` (animated while running, finalised with its duration). Outside a
  shutdown (``begin()`` never called) ``step()`` degrades to a plain log line.
- :func:`to_daemon_thread` — like ``asyncio.to_thread`` but on a daemon thread the interpreter
  abandons at exit. Use it for work that is safe to drop mid-flight when the process dies
  (TTS synthesis: the WAV is moot once we're quitting).
- :func:`disconnect_voice` — leave a voice channel without waiting out discord.py's post-leave
  confirmation (up to ``VoiceClient.timeout`` = 30s), which is moot at exit.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)

_RED = "\033[91m"
_RESET = "\033[0m"
_CLEAR_LINE = "\r\x1b[K"  # carriage return + erase-to-end (wipes the animated dots)


class ShutdownProgress:
    """Thread-safe ``[i/n]`` step display for the teardown.

    A painter daemon thread animates the *current* step's line; finishing a step rewrites
    that line with a checkmark + duration. All stdout writes happen under one lock so the
    painter can't interleave with a finalised line.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._done = 0
        self._label: str | None = None
        self._step_t0 = 0.0
        self._t0: float | None = None

    @property
    def active(self) -> bool:
        return self._t0 is not None

    def begin(self, total: int) -> None:
        """Announce the teardown and its step count; start the line painter."""
        with self._lock:
            self._total = total
            self._done = 0
            self._label = None
            self._t0 = time.monotonic()
            sys.stdout.write(f"{_RED}Shutting down — {total} Schritte:{_RESET}\n")
            sys.stdout.flush()
        threading.Thread(target=self._paint, daemon=True, name="shutdown-painter").start()

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        """One named teardown stage. Without ``begin()`` (no shutdown running, e.g. a cog
        unloaded at runtime) it only logs — no console step lines."""
        if not self.active:
            log.info("teardown: %s", label)
            yield
            return
        with self._lock:
            self._label = label
            self._step_t0 = time.monotonic()
            idx = self._done + 1
        try:
            yield
        finally:
            dur = time.monotonic() - self._step_t0
            with self._lock:
                self._done += 1
                self._label = None
                sys.stdout.write(f"{_CLEAR_LINE}  [{idx}/{self._total}] {label} ✓ ({dur:.1f}s)\n")
                sys.stdout.flush()

    def finish(self) -> None:
        """Final summary line; also names abandoned daemon work (e.g. a dropped synth)."""
        if not self.active:
            return
        total_s = time.monotonic() - (self._t0 or 0.0)
        abandoned = inflight_daemon_threads()
        note = f" — {abandoned} laufende TTS/Hintergrund-Arbeit(en) verworfen" if abandoned else ""
        with self._lock:
            self._t0 = None  # stops the painter
            done, total = self._done, self._total
            sys.stdout.write(
                f"{_CLEAR_LINE}{_RED}Shutdown fertig: {done}/{total} Schritte in "
                f"{total_s:.1f}s{note}{_RESET}\n"
            )
            sys.stdout.flush()

    def _paint(self) -> None:
        for dots in itertools.cycle(("   ", ".  ", ".. ", "...")):
            with self._lock:
                if self._t0 is None:
                    return
                if self._label is not None:
                    sys.stdout.write(
                        f"{_CLEAR_LINE}  [{self._done + 1}/{self._total}] {self._label} {dots}"
                    )
                    sys.stdout.flush()
            time.sleep(0.3)


progress = ShutdownProgress()


_inflight = 0
_inflight_lock = threading.Lock()


def inflight_daemon_threads() -> int:
    """How many ``to_daemon_thread`` jobs are still running (read by ``finish()``)."""
    with _inflight_lock:
        return _inflight


async def to_daemon_thread(fn: Callable[..., Any], /, *args: Any) -> Any:
    """Run ``fn(*args)`` on a daemon thread and await its result.

    Unlike ``asyncio.to_thread`` (default executor, non-daemon threads the interpreter joins
    at exit), the thread is abandoned when the process quits — so an in-flight call can never
    delay shutdown. Only for work whose result is worthless once we're exiting.
    """
    global _inflight
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def _set(result: Any = None, exc: BaseException | None = None) -> None:
        if fut.cancelled():
            return
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(result)

    def _run() -> None:
        global _inflight
        try:
            result = fn(*args)
        except BaseException as exc:  # delivered to the awaiter, not swallowed
            outcome: tuple[Any, BaseException | None] = (None, exc)
        else:
            outcome = (result, None)
        finally:
            with _inflight_lock:
                _inflight -= 1
        try:
            loop.call_soon_threadsafe(_set, *outcome)
        except RuntimeError:
            pass  # loop already closed (process exiting) — the result is moot

    with _inflight_lock:
        _inflight += 1
    threading.Thread(target=_run, daemon=True, name="dm-daemon-worker").start()
    return await fut


VOICE_DISCONNECT_TIMEOUT = 2.0  # s — bound discord.py's post-leave confirmation wait at exit


async def disconnect_voice(vc: Any, timeout: float = VOICE_DISCONNECT_TIMEOUT) -> bool:
    """Leave a voice channel without letting discord.py's post-leave *confirmation* stall exit.

    ``VoiceClient.disconnect(force=True)`` does the real leave first (closes the voice websocket
    and UDP socket — the bot leaves the channel immediately), then awaits a gateway
    ``voice_state_update`` confirmation for up to ``VoiceClient.timeout`` (30s, see discord
    ``voice_state.py``). That wait only guards a disconnect→immediate-reconnect race, which is
    moot when we're quitting — so we bound it.

    Returns ``True`` if it confirmed in time, ``False`` if the confirmation wait was abandoned
    (the leave itself already happened). Real disconnect errors propagate to the caller.

    Detecting the abandonment is subtle, so we don't go through ``asyncio.wait_for``. When ``wait_for``
    times out it *cancels* the inner ``disconnect``, but discord.py's confirmation wait **catches that
    ``CancelledError`` and returns normally** — still running its own ``cleanup()``, so the leave stays
    clean. A cancelled coroutine that swallows the cancel and returns makes ``wait_for`` *return*
    rather than raise ``asyncio.TimeoutError`` (Python 3.12), so keying off ``TimeoutError`` left the
    ``False`` branch — and the caller's "abandoned at shutdown" warning — dead against the real
    library; deciding by elapsed time instead is flaky right at the ``timeout`` boundary. Instead we
    run the disconnect as a task and ask ``asyncio.wait`` whether it *finished within the window*:
    finished → confirmed (re-raise any real error); still pending → the leave already happened, so we
    cancel the lingering confirmation wait (its swallowed-cancel cleanup runs) and report abandoned.
    """
    task = asyncio.ensure_future(vc.disconnect(force=True))
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        task.result()  # re-raise a real disconnect error; a clean confirmation returns its value
        return True
    # Timed out: the network leave already happened inside disconnect; only the gateway confirmation
    # is still pending. Abandon it — discord.py swallows this cancel and still runs its cleanup().
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("voice disconnect cleanup raised after the confirmation wait was abandoned")
    return False
