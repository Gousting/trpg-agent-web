"""Deterministic consistency guard — is the DM answer allowed by the world state? (ADR 045)

Pure functions (``re`` only, no Discord/I-O/LLM), testable like ``rules/combat.py``.
:func:`check` flags two violations in this first cut: a **dead** NPC speaking (the scene card
renders him ``(tot)``) and a living **registered NPC speaking in a scene he isn't in**
(``npcs_here``). Mere *mention* of a dead/absent NPC is fine (memories, finding the body) —
only a speech *attribution* counts, and the heuristics are deliberately conservative
(false positives cost a full regeneration of latency; when in doubt, don't flag):

- present-tense attribution verbs only — Präteritum („Grendel sagte damals …") is how
  memories/recaps read, exactly the allowed mention case;
- quoted spans are stripped first, so recounting someone's words never flags;
- indefinite/quantified references („ein Kultist ruft") never flag — generic statblock names
  double as anonymous extras the DM may invent;
- multi-word names match per token, but tokens shared with another NPC/party name or common
  titles are dropped as ambiguous; names match case-sensitively (proper nouns).

``DMBrain.respond``/``redo`` call this via an injected checker and regenerate **once** with
:func:`retry_nudge_de` appended; a still-failing retry is delivered anyway (fail-open — the
guard must never block the session). The streaming path can only log (audio already played).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # duck-typed at runtime — keeps llm/ decoupled from memory/ and rag/
    from ..memory.state import WorldState
    from ..rag.adventure import Scene


@dataclass(frozen=True)
class Violation:
    """One consistency breach: ``kind`` is ``"dead"`` or ``"absent"``; ``hint_de`` is the
    concrete German correction the retry prompt carries."""

    kind: str
    npc: str
    hint_de: str


# Present-tense (3rd sg) speech verbs. Present only — see module docstring / ADR 045.
_SPEECH_VERBS = (
    "sagt|spricht|antwortet|ruft|flüstert|erwidert|entgegnet|fragt|murmelt|knurrt|zischt"
    "|brüllt|schreit|raunt|krächzt|meint|erklärt|verkündet|befiehlt|warnt|wispert"
)

# A name preceded by one of these refers to *some* such figure, not the registered NPC
# („ein Kultist ruft" — the DM may invent anonymous extras, ADR 045).
_INDEFINITE_BEFORE = re.compile(
    r"(?:\b(?:ein|eine|einen|einem|einer|eines|kein|keine|keinem|keinen|keiner"
    r"|zwei|drei|vier|fünf|einige|mehrere|viele|weitere|weiterer|weiteres"
    r"|andere|anderer|anderes|jeder|jede|jedes)\s+)$",
    re.IGNORECASE,
)

# Paired quote spans; the inner content is blanked (quote chars kept) so attribution verbs
# *outside* the quotes still match. Unpaired quotes are left alone (rare; residual risk noted
# in ADR 045).
_QUOTE_SPANS = (
    re.compile(r"„[^“”]*[“”]"),
    re.compile(r"»[^«]*«"),
    re.compile(r'"[^"]*"'),
)

# Name tokens that are titles/particles, never a usable alias on their own.
_TOKEN_STOPLIST = {
    "der", "die", "das", "dem", "den", "des", "von", "vom", "zu", "zur", "zum", "van",
    "lord", "lady", "herr", "frau", "doktor", "magos", "captain", "hauptmann", "sergeant",
    "inquisitor", "bruder", "schwester", "vater", "mutter", "meister", "meisterin", "sankt",
}


def _strip_quotes(text: str) -> str:
    """Blank the content of paired quote spans (keep the quote characters, preserve length)
    so speech *inside* a quotation can't be mistaken for narration."""

    def _blank(m: re.Match) -> str:
        s = m.group(0)
        return s[0] + " " * (len(s) - 2) + s[-1]

    for pattern in _QUOTE_SPANS:
        text = pattern.sub(_blank, text)
    return text


def _name_variants(name: str, ambiguous_tokens: set[str]) -> list[str]:
    """The strings that count as a reference to ``name``: the full name, plus (for multi-word
    names) each token that is long enough, capitalized, no title/particle and not ambiguous
    (shared with another NPC or a party member)."""
    name = name.strip()
    if not name:
        return []
    tokens = re.split(r"[\s\-]+", name)
    if len(tokens) == 1 and name.lower() in ambiguous_tokens:
        # a single-word name that is also part of another NPC's name (Kultist vs Verfluchter
        # Kultist) or a party member's — any match is ambiguous, never flag it
        return []
    variants = [name]
    if len(tokens) > 1:
        for tok in tokens:
            if (
                len(tok) >= 3
                and tok[0].isupper()
                and tok.lower() not in _TOKEN_STOPLIST
                and tok.lower() not in ambiguous_tokens
            ):
                variants.append(tok)
    return variants


def _speech_patterns(variant: str) -> list[re.Pattern]:
    """The attribution patterns for one name variant (see module docstring for why each is
    shaped the way it is)."""
    n = re.escape(variant)
    return [
        # <Name> [up to two lowercase words] <verb> — a lowercase-only gap can't introduce a
        # new capitalized subject, so the attribution stays unambiguous.
        re.compile(rf"\b{n}(?:\s+[a-zäöüß]+){{0,2}}\s+(?:{_SPEECH_VERBS})\b"),
        # „…", <verb> [der/die/das] <Name> — inverted attribution after a quote.
        re.compile(rf"\b(?:{_SPEECH_VERBS})\s+(?:der\s+|die\s+|das\s+)?{n}\b"),
        # Script style at line start: <Name>: „…" — the opening quote is required, so a bare
        # "Name: …" list line never triggers.
        re.compile(rf"^\s*{n}\s*:\s*[„»\"‚']", re.MULTILINE),
    ]


def _speaks(text: str, variants: list[str]) -> bool:
    """True when ``text`` (already quote-stripped) attributes present-tense speech to one of
    the name ``variants`` — skipping indefinite references („ein Kultist ruft")."""
    for variant in variants:
        for pattern in _speech_patterns(variant):
            for m in pattern.finditer(text):
                if _INDEFINITE_BEFORE.search(text[: m.start()]):
                    continue
                return True
    return False


def _ambiguous_tokens(state: WorldState) -> set[str]:
    """Lowercased name tokens that occur in more than one NPC name or in a party-member name —
    a match on such a token can't be attributed to one NPC, so it never flags."""
    counts: dict[str, int] = {}
    for npc in state.npcs:
        for tok in set(re.split(r"[\s\-]+", npc.name.strip().lower())):
            if tok:
                counts[tok] = counts.get(tok, 0) + 1
    ambiguous = {tok for tok, n in counts.items() if n > 1}
    for member in state.characters:
        for tok in re.split(r"[\s\-]+", member.name.strip().lower()):
            if tok:
                ambiguous.add(tok)
    return ambiguous


def check(text: str, world_state: WorldState | None, scene: Scene | None) -> list[Violation]:
    """Check a DM answer against the world state; return the violations (empty = deliver).

    ``scene`` is the active scene card or ``None`` (no adventure / unknown pointer) — the
    absent-NPC check only runs with a scene (without one there is no notion of "here").
    The dead check runs regardless. Pure, conservative, never raises on odd inputs.
    """
    if not text or world_state is None or not getattr(world_state, "npcs", None):
        return []
    stripped = _strip_quotes(text)
    ambiguous = _ambiguous_tokens(world_state)
    violations: list[Violation] = []
    here = None
    if scene is not None:
        here = {n.strip().lower() for n in scene.npcs_here}
    for npc in world_state.npcs:
        variants = _name_variants(npc.name, ambiguous)
        if not variants:
            continue
        if npc.wounds <= 0:
            if _speaks(stripped, variants):
                violations.append(Violation(
                    kind="dead", npc=npc.name,
                    hint_de=(f"{npc.name} ist tot und kann nicht sprechen oder handeln. "
                             f"Erzähle die Szene ohne wörtliche Rede oder Handlung von {npc.name}."),
                ))
        elif here is not None and npc.name.strip().lower() not in here:
            if _speaks(stripped, variants):
                violations.append(Violation(
                    kind="absent", npc=npc.name,
                    hint_de=(f"{npc.name} ist in dieser Szene nicht anwesend und darf nicht "
                             f"sprechen. Erzähle die Szene ohne {npc.name}."),
                ))
    return violations


def retry_nudge_de(violations: list[Violation]) -> str:
    """The German correction appended to the regenerate prompt (same mechanism as the
    echo/intro nudges): concrete, one line per violation."""
    hints = " ".join(v.hint_de for v in violations)
    return f"KORREKTUR: Deine Antwort widersprach dem Spielzustand. {hints}"
