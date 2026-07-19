"""System profile loader — the *data* half of the generic rules engine (ADR 005).

A profile (``data/systems/<name>.json``) declares one ruleset's core mechanic: dice,
resolution kind, where the target comes from, how degrees of success are computed, the
difficulty ladder, and the character schema. The engine (:mod:`dmbot.rules.engine`) reads
these and stays game-agnostic — IM is just the first profile, nothing is hardcoded.

Pure data + a thin typed wrapper, so it unit-tests without Discord or the LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Repo data dir: this file is dmbot/rules/profile.py → parents[2] is the repo root.
_DATA_SYSTEMS = Path(__file__).resolve().parents[2] / "data" / "systems"

_REQUIRED = ("name", "dice", "resolution")


class ProfileError(ValueError):
    """A system profile is missing or malformed — raised with a human-readable reason."""


@dataclass(frozen=True, slots=True)
class SystemProfile:
    """One ruleset's declarative mechanic. Build via :meth:`from_dict` / :func:`load`."""

    name: str
    dice: str
    resolution: str
    display_name: str = ""
    target_source: str = "skill_value"
    degrees: str = "tens_difference"
    default_difficulty: str = ""
    difficulty_ladder: dict[str, int] = field(default_factory=dict)
    difficulty_aliases: dict[str, str] = field(default_factory=dict)
    crit: str = ""
    auto_success_max: int = 0   # rolls <= this always succeed (0 = no band). IM: 5.
    auto_fail_min: int = 0      # rolls >= this always fail (0 = no band). IM: 96.
    damage: "str | dict" = ""   # free-text ("weapon_damage + SL") or structured, per architecture §9
    combat: dict = field(default_factory=dict)  # attack skills, weapon damage table, soak source (§7/§9)
    psyker: dict = field(default_factory=dict)   # psychic powers catalog + Warp/Perils tables (ADR 022)
    augmetics: dict = field(default_factory=dict)  # augmetics catalog + limit (ADR 023)
    character_schema: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)  # full source dict, for forward-compat reads

    @classmethod
    def from_dict(cls, data: dict) -> "SystemProfile":
        missing = [k for k in _REQUIRED if not data.get(k)]
        if missing:
            raise ProfileError(f"profile is missing required key(s): {', '.join(missing)}")
        ladder = data.get("difficulty_ladder", {}) or {}
        if not all(isinstance(v, int) for v in ladder.values()):
            raise ProfileError("difficulty_ladder values must all be integers")
        return cls(
            name=data["name"],
            dice=data["dice"],
            resolution=data["resolution"],
            display_name=data.get("display_name", data["name"]),
            target_source=data.get("target_source", "skill_value"),
            degrees=data.get("degrees", "tens_difference"),
            default_difficulty=data.get("default_difficulty", ""),
            difficulty_ladder=dict(ladder),
            difficulty_aliases={k.lower(): v for k, v in (data.get("difficulty_aliases", {}) or {}).items()},
            crit=data.get("crit", ""),
            auto_success_max=int(data.get("auto_success_max", 0)),
            auto_fail_min=int(data.get("auto_fail_min", 0)),
            damage=data.get("damage", "") or "",
            combat=dict(data.get("combat", {}) or {}),
            psyker=dict(data.get("psyker", {}) or {}),
            augmetics=dict(data.get("augmetics", {}) or {}),
            character_schema=dict(data.get("character_schema", {}) or {}),
            raw=dict(data),
        )

    def difficulty_modifier(self, name: str | None) -> int | None:
        """Map a difficulty name to its modifier via the profile ladder (case-insensitive,
        aliases honoured). ``None`` input → the default difficulty. Returns ``None`` for an
        unknown word so the caller can fall back to the default."""
        if name is None:
            name = self.default_difficulty
        if not name:
            return None
        key = name.strip()
        # exact (case-insensitive) ladder hit
        for label, mod in self.difficulty_ladder.items():
            if label.lower() == key.lower():
                return mod
        # alias → canonical label
        canon = self.difficulty_aliases.get(key.lower())
        if canon is not None:
            for label, mod in self.difficulty_ladder.items():
                if label.lower() == canon.lower():
                    return mod
        return None

    def canonical_difficulty(self, name: str | None) -> str | None:
        """The canonical ladder label for ``name`` (resolving aliases/case), or ``None``."""
        if name is None:
            name = self.default_difficulty
        if not name:
            return None
        key = name.strip()
        for label in self.difficulty_ladder:
            if label.lower() == key.lower():
                return label
        canon = self.difficulty_aliases.get(key.lower())
        if canon is not None:
            for label in self.difficulty_ladder:
                if label.lower() == canon.lower():
                    return label
        return None

    def difficulty_names(self) -> list[str]:
        """Canonical ladder labels, hardest first only by declaration order — for the prompt."""
        return list(self.difficulty_ladder)

    # -- catalog helpers (shared by powers / augmetics / weapons) --------------------------
    # One case-insensitive scan over a {name: value} catalog, so the public lookups below stay
    # identical in shape and behaviour but don't repeat the strip/lower loop.

    @staticmethod
    def _catalog_match(catalog: dict, name: str) -> tuple[str, object] | None:
        """Case-insensitive scan of a ``{name: value}`` catalog. Returns the ``(found_name, value)``
        pair with the catalog's original casing, or ``None``."""
        key = name.strip().lower()
        for cname, value in (catalog or {}).items():
            if cname.strip().lower() == key:
                return cname, value
        return None

    @classmethod
    def _catalog_lookup(cls, catalog: dict, name: str) -> dict | None:
        """Lookup in a ``{name: stats}`` catalog, returning the matched entry as
        ``{"name": <found>, **(stats or {})}`` (original casing preserved), or ``None``."""
        match = cls._catalog_match(catalog, name)
        if match is None:
            return None
        cname, stats = match
        return {"name": cname, **(stats or {})}

    @staticmethod
    def _catalog_names(catalog: dict) -> list[str]:
        """All catalogued names in declaration order."""
        return list(catalog or {})

    # -- combat (Phase 9: auto damage) ----------------------------------------------------
    # All optional: a profile without a "combat" block simply has no auto-damage flow (the test
    # still rolls and reports, but no wounds are applied). Keeps the engine system-agnostic.

    def combat_enabled(self) -> bool:
        return bool(self.combat.get("attack_skills"))

    def is_attack_skill(self, skill: str | None) -> bool:
        """Is ``skill`` one the profile treats as an attack (success → roll & apply damage)?"""
        if not skill:
            return False
        key = skill.strip().lower()
        return any(key == s.strip().lower() for s in self.combat.get("attack_skills", []))

    def weapon_damage(self, weapon: str | None) -> str | None:
        """Damage notation for a named weapon from the profile's weapon table (case-insensitive),
        or ``None`` if the weapon is unknown (caller falls back to :meth:`default_damage`)."""
        if not weapon:
            return None
        match = self._catalog_match(self.combat.get("weapons", {}) or {}, weapon)
        return None if match is None else match[1]

    def default_damage(self) -> str:
        """Fallback damage notation for an unknown/unspecified weapon (e.g. '1d10')."""
        return self.combat.get("default_damage", "") or ""

    def soak_characteristic(self) -> str:
        """Which characteristic feeds soak (IM: 'Tgh' → Toughness Bonus). Empty = no characteristic
        soak."""
        return (self.combat.get("soak", {}) or {}).get("characteristic", "") or ""

    def soak_mode(self) -> str:
        """How the soak characteristic becomes a bonus: 'tens' (IM Toughness Bonus = tens digit) or
        'value'. Default 'tens'."""
        return (self.combat.get("soak", {}) or {}).get("mode", "tens") or "tens"

    # -- psyker / Warp (ADR 022) ----------------------------------------------------------
    # All optional: a profile without a "psyker" block has no Warp flow. The engine reads the
    # power's Warp Rating + Difficulty and the Perils/Phenomena tables from here and stays
    # system-agnostic — the catalog is data, not code.

    def psyker_enabled(self) -> bool:
        return bool(self.psyker.get("powers"))

    def psyker_test_skill(self) -> str:
        """The Skill rolled to Manifest a power (IM: 'Psychic Mastery'). Play-language name."""
        return self.psyker.get("test_skill", "") or ""

    def psyker_purge_skill(self) -> str:
        """The Skill rolled to Purge Warp Charge (IM: 'Discipline (Psychic)')."""
        return self.psyker.get("purge_skill", "") or ""

    def power(self, name: str | None) -> dict | None:
        """A psychic power's stat block from the catalog (case-insensitive), or ``None``."""
        if not name:
            return None
        return self._catalog_lookup(self.psyker.get("powers", {}) or {}, name)

    def power_names(self) -> list[str]:
        """All catalogued power names (declaration order) — for the prompt/classifier."""
        return self._catalog_names(self.psyker.get("powers", {}) or {})

    def warp_threshold(self, willpower_value: int | None) -> int:
        """Warp Threshold from a character's governing characteristic (IM: Willpower Bonus =
        tens digit of Willpower). Driven by ``psyker.threshold`` = {characteristic, mode}."""
        if willpower_value is None:
            return 0
        cfg = self.psyker.get("threshold", {}) or {}
        mode = cfg.get("mode", "tens")
        return willpower_value // 10 if mode == "tens" else int(willpower_value)

    def threshold_characteristic(self) -> str:
        """Which characteristic feeds the Warp Threshold (IM: 'Wil')."""
        return (self.psyker.get("threshold", {}) or {}).get("characteristic", "") or ""

    def perils_table(self) -> list[dict]:
        """The d100 Perils of the Warp table (banded rows with name/effect/corruption)."""
        return list(self.psyker.get("perils_table", []) or [])

    def phenomena_table(self) -> list[dict]:
        """The d100 Psychic Phenomena table (banded rows; some redirect to perils/phenomena)."""
        return list(self.psyker.get("phenomena_table", []) or [])

    # -- augmetics / cybernetics (ADR 023) ------------------------------------------------
    # All optional, like the combat/psyker blocks. Augmetics are passive (no roll): the engine
    # applies their 'armour' (soak) and 'characteristic' effects; 'skill_sl'/'special' effects are
    # narrative (the DM applies them, prose from RAG). A profile with no 'augmetics' block has none.

    def augmetics_enabled(self) -> bool:
        return bool(self.augmetics.get("catalog"))

    def augmetic(self, name: str | None) -> dict | None:
        """An augmetic's stat block from the catalog (case-insensitive), or ``None``."""
        if not name:
            return None
        return self._catalog_lookup(self.augmetics.get("catalog", {}) or {}, name)

    def augmetic_names(self) -> list[str]:
        """All catalogued augmetic names (declaration order) — for the prompt / creation form."""
        return self._catalog_names(self.augmetics.get("catalog", {}) or {})

    def augmetic_limit(self, toughness_value: int | None) -> int:
        """Max number of augmetics (IM: Toughness Bonus = tens of Toughness; 'Flesh is Weak'
        doubles it, applied by the caller). 0 if no characteristic value is known."""
        if toughness_value is None:
            return 0
        cfg = self.augmetics.get("limit", {}) or {}
        mode = cfg.get("mode", "tens")
        return toughness_value // 10 if mode == "tens" else int(toughness_value)


def systems_dir() -> Path:
    return _DATA_SYSTEMS


def load(name: str, *, systems_root: Path | None = None) -> SystemProfile:
    """Load and validate the profile ``data/systems/<name>.json``.

    Raises :class:`ProfileError` if the file is absent or malformed, with a reason the
    caller can surface (the cog logs it and runs rules-less rather than crashing).
    """
    root = systems_root or _DATA_SYSTEMS
    path = root / f"{name}.json"
    if not path.is_file():
        raise ProfileError(f"no system profile at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"could not read profile {path}: {exc}") from exc
    return SystemProfile.from_dict(data)
