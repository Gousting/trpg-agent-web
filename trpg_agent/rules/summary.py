"""Build a short, player-facing rules summary (German) from the active system profile.

System-agnostic: it reads the declarative :class:`~dmbot.rules.profile.SystemProfile` fields
(dice, resolution, the difficulty ladder, degrees rule, auto-bands, crit, damage) and renders the
*essentials* a player needs at the table — so the `!rules` command stays in sync with whatever
profile is loaded, never a hand-maintained copy. Pure function → unit-testable without Discord.
"""

from __future__ import annotations

import json

from .profile import SystemProfile


def _ladder_lines(profile: SystemProfile) -> list[str]:
    # Easiest first (highest modifier on top), matching how a difficulty table usually reads.
    items = sorted(profile.difficulty_ladder.items(), key=lambda kv: kv[1], reverse=True)
    return [f"• **{name}**: {mod:+d}" for name, mod in items]


def rules_pages_de(profile: SystemProfile) -> list[tuple[str, str]]:
    """Return the rules essentials as ``(page_title, page_body)`` pairs, German, in reading order.
    Only includes a page when the profile actually declares the relevant fields."""
    pages: list[tuple[str, str]] = []
    dice = profile.dice

    # 1) How a test works — phrased per the resolution kind.
    if profile.resolution == "roll_under":
        how = (
            f"Gewürfelt wird **{dice}** — du willst **gleich oder unter** deinen Zielwert rollen.\n\n"
            "**Zielwert** = dein Fertigkeits-/Merkmalswert + der Schwierigkeits-Modifikator.\n\n"
            "Die **Spielleitung würfelt für dich**: ein Würfel-Knopf erscheint, das Ergebnis und die "
            "Erfolgsgrade stehen sofort da."
        )
    elif profile.resolution == "roll_over":
        how = (
            f"Gewürfelt wird **{dice}** — du willst **gleich oder über** deinen Zielwert rollen.\n\n"
            "**Zielwert** = dein Wert + der Schwierigkeits-Modifikator. Die Spielleitung würfelt für dich."
        )
    else:
        how = f"Gewürfelt wird **{dice}** (Auswertung: {profile.resolution}). Die Spielleitung würfelt für dich."
    pages.append(("Wie eine Probe läuft", how))

    # 2) Difficulty ladder.
    if profile.difficulty_ladder:
        body = "Die Spielleitung wählt die Schwierigkeit; ihr Modifikator zählt auf den Zielwert:\n\n"
        body += "\n".join(_ladder_lines(profile))
        if profile.default_difficulty:
            body += f"\n\nStandard, wenn nichts gesagt wird: **{profile.default_difficulty}**."
        pages.append(("Schwierigkeitsgrade", body))

    # 3) Success degrees, auto-bands, crit/fumble.
    lines: list[str] = []
    if profile.degrees == "tens_difference":
        lines.append(
            "**Erfolgsgrade (EG)** = Zehnerstelle des Zielwerts − Zehnerstelle des Wurfs (Betrag). "
            "Mehr EG = klarerer Ausgang."
        )
    if profile.auto_success_max:
        lines.append(f"**01–{profile.auto_success_max:02d}**: immer Erfolg (knapp).")
    if profile.auto_fail_min:
        lines.append(f"**{profile.auto_fail_min}–00**: immer Fehlschlag (knapp).")
    if profile.crit == "doubles":
        lines.append(
            "**Doppelzahlen** (11, 22, … 99, 00): im Kampf kritischer Treffer (bei Erfolg) "
            "bzw. Patzer (bei Fehlschlag)."
        )
    if lines:
        pages.append(("Erfolg, Erfolgsgrade & Sonderfälle", "\n\n".join(lines)))

    # 4) Damage.
    dmg = profile.damage if isinstance(profile.damage, str) else json.dumps(profile.damage, ensure_ascii=False)
    if dmg:
        pages.append(("Schaden", f"**Schaden:** {dmg}"))

    return pages
