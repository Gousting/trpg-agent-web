"""GM-side "director" instructions for the opening turns (pure prompt text).

Extracted from ``orchestrator.py`` (ADR 034). :func:`build_opening_director_msg` drives the short
``!start`` briefing; :func:`build_intro_director_msg` drives the longer ``!intro`` monologue
(ADR 031) and embeds the party roster. The DM never reads these aloud — they instruct the model to
OPEN the session. The cog (``dmcog.py``) imports these; nothing in ``DMBrain`` calls them (it
receives the resulting ``director_msg`` as a parameter).
"""

from __future__ import annotations


# --- Opening briefing (!start) ------------------------------------------------------------------

# The director instruction that drives the !start opening turn. It is a GM-side ("director")
# message, NOT a player line: it tells the model to OPEN the session out loud so the table knows
# who they are and what their mission is (the first-session complaint: the bot "hat am Anfang
# nicht gesagt, was abgeht"). The concrete content — the Halikarn briefing, the three leads — is
# NOT spelled out here: it lives in the start scene's card (## Aktuelle Szene + guidance_de),
# which the system prompt already carries. So this only has to point the model at that scene and
# hold it to the persona's voice. Phrased as an instruction to the GM, never read aloud.
OPENING_DIRECTOR_MSG = (
    "[Regie] Eröffne jetzt die Sitzung: Spiele die Auftrags-/Eröffnungsszene aus deiner "
    "aktuellen Szene. Mach den Spielenden klar, wer sie sind und was ihr Auftrag ist, und "
    "deute die ersten Spuren über ein Detail der Umgebung an — nicht als Aufzählung. Halte "
    "dich an die Spielleitungs-Stimme (2–4 Sätze). Verlange keine Probe."
)


def build_opening_director_msg() -> str:
    """The GM-side director instruction for the ``!start`` opening turn (pure, unit-testable).

    Kept as a function so the cog never inlines the prompt text and a test can assert its shape
    (it must read as a GM/director instruction, not as a player action, and must forbid a dice
    test on the briefing)."""
    return OPENING_DIRECTOR_MSG


# --- Intro monologue (!intro) ------------------------------------------------------------------

# The director instruction for the one-time !intro opening MONOLOGUE (ADR 031). Unlike the short
# !start briefing (OPENING_DIRECTOR_MSG, 2–4 sentences), this asks for one coherent opening monologue
# that establishes place + how they arrived + the mission AND gives each player character a personal
# beat. The concrete adventure content (place, mission, leads) lives in the start scene's card +
# adventure summary already in the system prompt; the party roster is embedded here (it rides in the
# turn's user message so the ADR-019 prompt order is untouched). GM-side instruction, never read aloud.
_INTRO_DIRECTOR_HEAD = (
    "[Regie] Eröffne jetzt die Sitzung mit einem zusammenhängenden Eröffnungs-Monolog "
    "(mehrere Absätze, kein Aufzählen, keine Stichpunkte). Beginne sofort als Erzähler mitten "
    "in der Szene — schreibe NICHT, dass du die Sitzung eröffnest oder was du als Spielleitung "
    "gerade tust, und kündige den Monolog nicht an. Etabliere zuerst, wo die Gruppe ist und wie "
    "sie hergekommen ist, dann die Lage und ihren Auftrag — stütze dich dabei auf deine aktuelle "
    "Szene und die Abenteuer-Zusammenfassung."
)
_INTRO_DIRECTOR_CHARS = (
    "Beziehe danach jede der folgenden Figuren mit einem kurzen, persönlichen Moment ein "
    "(sprich sie namentlich an und knüpfe an ihre Herkunft, ihr Wesen und ihre Beweggründe an) — "
    "webe das ins Bild ein, lies nichts davon wörtlich vor und sprich geheime oder rein private "
    "Ziele höchstens andeutend aus:\n\n{roster}"
)
_INTRO_DIRECTOR_TAIL = (
    "Bleib durchgehend in der Spielleitungs-Stimme und nimm dir Raum — das ist der Auftakt, er "
    "darf deutlich länger sein als ein normaler Zug. Schließe ihn stimmungsvoll ab und lade die "
    "Gruppe in die Szene ein (etwa welche Spur sie zuerst verfolgt); brich nicht nach wenigen "
    "Sätzen mit einer knappen „Was tut ihr?\"-Frage ab. Verlange keine Probe."
)


def build_intro_director_msg(roster_de: str = "") -> str:
    """The GM-side director instruction for the ``!intro`` opening monologue (pure, unit-testable).

    Asks for one coherent opening monologue (place + how they arrived + mission) and weaves in each
    player character via the embedded ``roster_de`` block (from ``CharacterStore.intro_roster_de``).
    With an empty roster it degrades to the place/mission monologue alone. Kept as a function so the
    cog never inlines the prompt text and a test can assert its shape (one monologue, every figure
    involved, no dice)."""
    msg = _INTRO_DIRECTOR_HEAD
    if roster_de.strip():
        msg += " " + _INTRO_DIRECTOR_CHARS.format(roster=roster_de.strip())
        msg += "\n\n" + _INTRO_DIRECTOR_TAIL
    else:
        msg += " " + _INTRO_DIRECTOR_TAIL
    return msg
