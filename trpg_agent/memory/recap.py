"""Session recaps — the 'narrative thread' half of memory (architecture §7b, golden rule #3).

The LLM *summarises* the session into a "Was bisher geschah" recap; code *stores* it (in the world
state) and re-injects it at the front of the next session's prompt. This keeps the story coherent
across restarts without dragging the whole raw history along.

This module is the pure prompt-building half (German system prompt + history → a transcript the
summariser reads). The LLM call itself lives in :meth:`dmbot.orchestrator.DMBrain.summarize`, which
owns the Ollama client and the per-channel history.
"""

from __future__ import annotations

# German: the recap is game content (play language), like the persona. It must read like a short
# "previously on …" for the players, not a meta report — facts only, no rules talk, no commentary.
RECAP_SYSTEM_DE = (
    "Du bist die Spielleitung eines Tabletop-Rollenspiels und schreibst eine kurze Zusammenfassung "
    "der bisherigen Sitzung — ein \"Was bisher geschah\", das die Gruppe beim nächsten Mal an den "
    "Stand erinnert.\n\n"
    "Regeln:\n"
    "- Schreibe 4–8 Sätze, dichte Prosa, in der Vergangenheitsform, auf Deutsch.\n"
    "- Nur was tatsächlich geschah: besuchte Orte, getroffene NSCs, Entscheidungen der Gruppe, "
    "Kämpfe und ihr Ausgang, offene Fäden.\n"
    "- Nenne die Charaktere bei ihren Namen.\n"
    "- Keine Würfel-/Regelsprache, keine Meta-Kommentare, keine Anrede an die Spielenden, keine "
    "Aufzählungspunkte. Erfinde nichts dazu — fasse nur den gegebenen Verlauf zusammen.\n"
    "- Ende mit dem offenen Faden, an dem es weitergeht."
)


def build_recap_user(history: list[dict[str, str]], prior_recap: str = "") -> str:
    """Render the per-channel chat history into a transcript for the summariser. Player turns are
    labelled ``Spieler``, the DM's narration ``Spielleitung``; ``[Würfel]``/``💥`` result lines that
    were fed back are kept (they mark what happened mechanically).

    ``prior_recap`` makes the recap *cumulative* (the auto-compaction trigger, D56): when the running
    history is about to be cleared, an earlier recap already covers what scrolled out of it. We feed
    that prior recap in as the lead-in so the new recap *supersedes and extends* it — nothing already
    summarised is lost. Empty when there's no prior recap (the plain `!wrap up` case)."""
    lines: list[str] = []
    for msg in history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        speaker = "Spielleitung" if role == "assistant" else "Spieler"
        lines.append(f"{speaker}: {content}")
    transcript = "\n".join(lines)
    prior_recap = (prior_recap or "").strip()
    if prior_recap:
        # The earlier recap is the "so far" the new summary must keep, then the fresh transcript is
        # what happened since. One combined recap comes back out, replacing the old one.
        return (
            "Bisherige Zusammenfassung (\"Was bisher geschah\"):\n"
            f"{prior_recap}\n\n"
            "Seitdem ist Folgendes geschehen:\n\n"
            f"{transcript}\n\n"
            "Schreibe EINE zusammenhängende, aktualisierte Zusammenfassung, die das Bisherige und "
            "das Neue vereint — nichts aus der bisherigen Zusammenfassung darf verloren gehen."
        )
    return (
        "Hier ist der Verlauf der bisherigen Sitzung. Fasse ihn als \"Was bisher geschah\" zusammen:\n\n"
        f"{transcript}"
    )
