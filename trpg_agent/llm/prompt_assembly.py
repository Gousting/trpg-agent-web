"""Single owner of the DM system-prompt assembly order (ADR 034 family — pure helpers).

``orchestrator._build_request`` used to inline the string-join that layers the prompt slices.
That made the prompt ORDER an implementation detail buried in a method that also reads caches
and builds the Ollama request. This module extracts ONLY the final join into one pure,
order-explicit, testable function — it does not read or compute any slice itself; the caller
passes the already-resolved strings in.

Memory order per docs/conventions.md: persona (core+tone) → recap → JSON state →
who-plays-whom → history. (RAG hits sit between state and the alias hint here; history is added
by the caller, not by this function.)
"""

from __future__ import annotations


def assemble_system_prompt(
    persona: str,
    *,
    recap: str | None = None,
    adventure: str | None = None,
    state_summary: str | None = None,
    npc_memory: str | None = None,
    rag: str | None = None,
    alias_hint: str | None = None,
) -> str:
    """Join the present prompt slices into one system prompt, in the fixed memory order
    ``persona → recap (wrapped) → adventure → state_summary → npc_memory → rag → alias_hint``.

    Each optional slice is included ONLY when truthy — both ``None`` and ``""`` are skipped,
    replicating the old ``if recap:`` chain in ``_build_request``. Slices are separated by exactly
    one blank line (``"\\n\\n"``). ``recap`` is wrapped in the German "Was bisher geschah" header;
    every other slice is appended verbatim. ``npc_memory`` (ADR 044) sits right after the hard
    state it colours, before the retrieved rulebook text.
    """
    parts = [persona]
    if recap:
        parts.append(
            "## 前情提要 "
            "（调查员已知内容——不要重新详细叙述）\n"
            f"{recap}"
        )
    if adventure:
        parts.append(adventure)
    if state_summary:
        parts.append(state_summary)
    if npc_memory:
        parts.append(npc_memory)
    if rag:
        parts.append(rag)
    if alias_hint:
        parts.append(alias_hint)
    return "\n\n".join(parts)
