"""Console + file logging for DMbot.

The **console** is curated for reading during play: a green theme (dark "diff added-line"
green) where transcripts (``📝``) render as a chat layout — speaker name in bright green, the
line in green — and the high-frequency pipeline chatter (the ``PCM ⟳`` heartbeats,
faster-whisper's per-utterance lines) is hidden from the console.

Two **files** (both opt-in via ``DM_LOG_FILE=1``, UTF-8, both kept token-light so they can be
pasted whole when debugging):
- ``logs/terminal.log`` — a plain (no-ANSI) **mirror of exactly what the console shows**, **reset
  on every start** (it only ever holds the current run; ``debug.log`` stays append).
- ``logs/debug.log`` — **more** detail for debugging (third-party INFO like the ``httpx`` request
  lines, full tracebacks), but the 2 s ``PCM ⟳`` heartbeat flood is collapsed to ~one-in-N so it
  stays small. This replaces the old single ``dmbot.log`` (which kept the heartbeat torrent and was
  unpasteable).

ANSI colours are enabled on the Windows console via ``colorama.just_fix_windows_console()``
(turns on virtual-terminal processing for the conhost that ``start_dmbot.bat`` opens).
"""

from __future__ import annotations

import logging
import re
import shutil
import textwrap
import time
from pathlib import Path

try:  # enable ANSI on the Windows console; harmless elsewhere / if missing
    import colorama

    colorama.just_fix_windows_console()
except Exception:  # pragma: no cover
    pass

_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
# Two opt-in debugging files (DM_LOG_FILE=1): a plain mirror of the console, and a fuller-but-
# -reduced debug log (heartbeat flood collapsed) — both pasteable without eating tokens.
_TERMINAL_FILE = _LOGS_DIR / "terminal.log"
_DEBUG_FILE = _LOGS_DIR / "debug.log"
# A clean, human-readable session transcript — just the conversation (player lines + DM answers)
# with timestamps, none of the debug chatter. Meant to be pasted whole to show "what went down".
_TRANSCRIPT_FILE = _LOGS_DIR / "transcript.log"

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"   # the darker "diff added-line" green — the ambient theme colour
_BGREEN = "\033[92m"  # bright green, for emphasis (speaker names)
_RED = "\033[91m"
_YELLOW = "\033[93m"

# Chat layout: timestamp + a 12-wide name + a dim metric, then the text. The hanging indent
# for wrapped lines is computed per line from the actual prefix width.
_NAME_W = 12
_GAP = "  "


def _short_name(name: str) -> str:
    """Trim the noisy ``dmbot.`` package prefix from a logger name so it costs fewer tokens when a
    log is pasted (``dmbot.voice.delivery`` → ``voice.delivery``). Third-party names (httpx,
    faster_whisper, discord.*) are left intact — there the full path tells you who logged it."""
    return name[len("dmbot.") :] if name.startswith("dmbot.") else name


class _ConsoleFormatter(logging.Formatter):
    """Green-themed console: transcripts as chat (green), warnings/errors highlighted."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        msg = record.getMessage()

        if msg.startswith("🎭"):  # the DM's answer — prominent, bright, hanging indent
            text = msg.split(" ", 1)[1] if " " in msg else msg
            label = "Spielleiter"[:_NAME_W]
            prefix = f"{ts}{_GAP}{label:>{_NAME_W}}{_GAP}"
            indent = len(prefix)
            cols = shutil.get_terminal_size((100, 24)).columns
            lines = textwrap.wrap(text, width=max(20, cols - indent - 1)) or [""]
            head = (
                f"{_DIM}{_GREEN}{ts}{_RESET}{_GAP}"
                f"{_BGREEN}{_BOLD}{label:>{_NAME_W}}{_RESET}{_GAP}{_BGREEN}{_BOLD}{lines[0]}{_RESET}"
            )
            rest = [f"{' ' * indent}{_BGREEN}{ln}{_RESET}" for ln in lines[1:]]
            return "\n".join([head, *rest])

        if msg.startswith("📝"):  # "📝 Name | clip·ms | text" → a chat line, hanging indent
            body = msg.split(" ", 1)[1] if " " in msg else msg
            name, metric, text = (body.split(" | ", 2) + ["", ""])[:3]
            name = name[:_NAME_W]
            # The text column sits after ts + name + the dim metric, so continuation lines
            # hang under the first word (indented to that column) rather than at the margin.
            prefix = f"{ts}{_GAP}{name:>{_NAME_W}}{_GAP}{metric}{_GAP}"
            indent = len(prefix)
            cols = shutil.get_terminal_size((100, 24)).columns
            lines = textwrap.wrap(text, width=max(20, cols - indent - 1)) or [""]
            head = (
                f"{_DIM}{_GREEN}{ts}{_RESET}{_GAP}"
                f"{_BGREEN}{_BOLD}{name:>{_NAME_W}}{_RESET}{_GAP}"
                f"{_DIM}{_GREEN}{metric}{_RESET}{_GAP}{_GREEN}{lines[0]}{_RESET}"
            )
            rest = [f"{' ' * indent}{_GREEN}{ln}{_RESET}" for ln in lines[1:]]
            return "\n".join([head, *rest])

        if msg.startswith("🗣"):  # utterance cut — secondary, dim green
            return f"{_DIM}{_GREEN}{ts}  {msg}{_RESET}"

        if record.levelno >= logging.WARNING:  # keep these loud, not green
            col = _RED if record.levelno >= logging.ERROR else _YELLOW
            return f"{_DIM}{ts}{_RESET} {col}{record.levelname:<7} {_short_name(record.name)}{_RESET} | {col}{msg}{_RESET}"

        if msg.startswith("logged in as"):  # the startup "ready" line (__main__) — a bit brighter
            return f"{_DIM}{_GREEN}{ts}{_RESET}{_GAP}{_BGREEN}{msg}{_RESET}"

        # INFO: the curated console only shows dmbot.* lines (see _ConsoleNoiseFilter), so the logger
        # name is redundant noise — drop it. The message (usually emoji-prefixed) stands on its own,
        # and pasted logs stay token-light.
        return f"{_DIM}{_GREEN}{ts}  {msg}{_RESET}"


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class _PlainMirrorFormatter(_ConsoleFormatter):
    """The exact console layout with ANSI colour stripped — a faithful, plain mirror for the file
    ``logs/terminal.log``. Reuses the console formatter so the file shows literally what the terminal
    showed, just without the escape codes."""

    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_RE.sub("", super().format(record))


class _DebugFormatter(logging.Formatter):
    """``%(name)s``-bearing format for ``logs/debug.log`` with the ``dmbot.`` prefix trimmed
    (:func:`_short_name`), to keep the pasted log token-light. Restores the record afterwards so the
    other handlers (which share the record) still see the full name."""

    def format(self, record: logging.LogRecord) -> str:
        original = record.name
        record.name = _short_name(record.name)
        try:
            return super().format(record)
        finally:
            record.name = original


class _ConsoleNoiseFilter(logging.Filter):
    """Keep the CONSOLE lean: only DMbot's own lines, plus any WARNING/ERROR from anywhere.

    Third-party INFO chatter (TTS/coqui synthesizer, httpx requests, faster_whisper, discord) and
    the 2 s PCM heartbeat are dropped from the console. The file handler (when enabled) has no
    filter, so a debug run still records the full detail."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_console_skip", False):
            return False  # flagged file-only (benign voice-recv unpack notices)
        msg = record.getMessage()
        if "PCM ⟳" in msg:
            return False  # the 2 s per-user heartbeat
        if "🪵" in msg:
            return False  # raw-LLM debug line — debug.log only, off the console + terminal mirror
        return record.levelno >= logging.WARNING or record.name.startswith("dmbot")


class _UnpackErrorThrottle(logging.Filter):
    """Collapse discord-ext-voice-recv's "Error unpacking packet" flood (console + file).

    The alpha voice-recv library can't parse some RTP one-byte extension headers
    (``_parse_bede_header`` → ``struct.error``) and logs one ERROR *with traceback* per bad
    packet. It is **benign** — that packet is dropped, audio keeps flowing — but it can torrent
    hundreds of identical tracebacks in a second and bury everything else. We let the first one
    through (so it's on record), then suppress the rest, emitting a running count every Nth so a
    genuine escalation is still visible. Attached to the logger, so it covers all handlers."""

    _N = 500

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if "Error unpacking packet" not in record.getMessage():
            return True
        self._count += 1
        record.exc_info = None  # drop the (identical, noisy) traceback
        record.args = None
        record._console_skip = True  # benign — keep it out of the lean console (file log only)
        if self._count == 1:
            record.msg = (
                "voice-recv could not unpack an RTP packet (benign alpha jitter — the packet is "
                "dropped, audio keeps flowing; further occurrences summarised, tracebacks hidden)"
            )
            return True
        if self._count % self._N == 0:
            record.msg = f"voice-recv has dropped {self._count} unparseable RTP packets (benign)"
            return True
        return False


class _HeartbeatThrottle(logging.Filter):
    """Collapse the 2 s ``PCM ⟳`` per-user heartbeat in the DEBUG file: keep the first, then one in
    every N. "Is audio still flowing?" stays answerable without the heartbeat torrenting hundreds of
    near-identical lines (the thing that made the old full log unpasteable). The console + the
    terminal mirror drop it entirely (``_ConsoleNoiseFilter``); this keeps a thinned trace in debug.log."""

    _N = 30  # ~once a minute at the 2 s cadence

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if "PCM ⟳" not in record.getMessage():
            return True
        self._count += 1
        return self._count == 1 or self._count % self._N == 0


class _TranscriptFilter(logging.Filter):
    """Pass only the conversation lines — player transcripts (``📝``) and DM answers (``🎭``) —
    so the transcript file stays a clean record of what was said, no debug chatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return msg.startswith("📝") or msg.startswith("🎭")


class _TranscriptFormatter(logging.Formatter):
    """Render one conversation line as ``HH:MM:SS  Speaker[ →DM]: text`` — timestamps kept, the
    debug metric (clip·ms) dropped. The ``→DM`` marker shows what was routed to the DM."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        msg = record.getMessage()
        if msg.startswith("📝"):  # "📝 Name | clip·ms[ →DM] | text"
            body = msg.split(" ", 1)[1] if " " in msg else msg
            name, metric, text = (body.split(" | ", 2) + ["", ""])[:3]
            marker = " →DM" if "→DM" in metric else ""
            return f"{ts}  {name}{marker}: {text}"
        # "🎭 <answer>" — the DM's turn
        text = msg.split(" ", 1)[1] if " " in msg else msg
        return f"{ts}  Spielleiter: {text}"


def setup_logging(
    level: str, *, to_file: bool = False, transcript_file: bool = False
) -> Path | None:
    """Install the console handler (always) and the file handler (only when ``to_file``).

    The console is kept lean (see :class:`_ConsoleNoiseFilter`). The full-detail file log is
    **off by default** — enable it with ``DM_LOG_FILE=1`` when you need to inspect a run after the
    window closes. Returns the log-file path when file logging is on, else ``None``."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    for handler in list(root.handlers):  # idempotent across restarts/tests
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(_ConsoleFormatter())
    console.addFilter(_ConsoleNoiseFilter())
    root.addHandler(console)

    log_file: Path | None = None
    if to_file:
        debug_fmt = _DebugFormatter(
            "%(asctime)s %(levelname)-7s %(name)s | %(message)s", datefmt="%H:%M:%S"
        )
        try:
            _LOGS_DIR.mkdir(parents=True, exist_ok=True)
            # 1) terminal.log — a plain (no-ANSI) mirror of exactly what the console shows: the
            #    curated, lean view (dmbot.* + WARNING/ERROR; no PCM heartbeat, no third-party INFO).
            #    Truncated on every start (mode="w") so it only ever holds the current run's console.
            term_h = logging.FileHandler(_TERMINAL_FILE, mode="w", encoding="utf-8")
            term_h.setFormatter(_PlainMirrorFormatter())
            term_h.addFilter(_ConsoleNoiseFilter())
            root.addHandler(term_h)
            # 2) debug.log — fuller detail for debugging (third-party INFO like httpx + tracebacks),
            #    but the PCM heartbeat flood is collapsed (one-in-N) so it stays token-light/pasteable.
            debug_h = logging.FileHandler(_DEBUG_FILE, mode="a", encoding="utf-8")
            debug_h.setFormatter(debug_fmt)
            debug_h.addFilter(_HeartbeatThrottle())
            root.addHandler(debug_h)
            log_file = _DEBUG_FILE
        except OSError:
            logging.getLogger("dmbot").warning(
                "could not open log files in %s — console only", _LOGS_DIR, exc_info=True
            )

    transcript_path: Path | None = None
    if transcript_file:
        try:
            _TRANSCRIPT_FILE.parent.mkdir(parents=True, exist_ok=True)
            transcript_h = logging.FileHandler(_TRANSCRIPT_FILE, mode="a", encoding="utf-8")
            transcript_h.setFormatter(_TranscriptFormatter())
            transcript_h.addFilter(_TranscriptFilter())  # only 📝/🎭 — the conversation
            root.addHandler(transcript_h)
            transcript_path = _TRANSCRIPT_FILE
        except OSError:
            logging.getLogger("dmbot").warning(
                "could not open transcript file %s", _TRANSCRIPT_FILE, exc_info=True
            )

    # discord.py gateway/voice logs are noisy; keep them civil (console + file).
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.ext.voice_recv.opus").setLevel(logging.ERROR)
    # Collapse the benign "Error unpacking packet" RTP-parse flood (alpha voice-recv bug).
    logging.getLogger("discord.ext.voice_recv.reader").addFilter(_UnpackErrorThrottle())

    where = (
        f"logs: {_TERMINAL_FILE.name} (terminal mirror) + {_DEBUG_FILE.name} (reduced debug)"
        if log_file else "file logging off (set DM_LOG_FILE=1)"
    )
    if transcript_path:
        where += f"; transcript {transcript_path}"
    logging.getLogger("dmbot").info(
        "=== DMbot starting (%s) — %s ===", time.strftime("%Y-%m-%d %H:%M:%S"), where
    )
    return log_file
