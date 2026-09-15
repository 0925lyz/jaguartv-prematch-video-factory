from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


BRASILEIRAO_SERIE_A_ID = 71
COPA_LIBERTADORES_ID = 13
COPA_SUDAMERICANA_ID = 11

COMPETITION_IDS = {
    BRASILEIRAO_SERIE_A_ID: "brasileirao_serie_a",
    COPA_LIBERTADORES_ID: "copa_libertadores",
    COPA_SUDAMERICANA_ID: "copa_sudamericana",
}

COMPETITION_ALIASES = {
    "brasileirao_serie_a": {
        "brasileirao serie a",
        "campeonato brasileiro serie a",
        "brazil serie a",
    },
    "copa_libertadores": {
        "conmebol libertadores",
        "copa conmebol libertadores",
        "copa libertadores",
        "libertadores",
        "taca libertadores da america",
    },
    "copa_sudamericana": {
        "conmebol sudamericana",
        "copa conmebol sudamericana",
        "copa sudamericana",
        "copa sul americana",
        "sul americana",
        "sudamericana",
    },
}

_STAGE_WORDS = {
    "group", "groups", "league", "stage", "fase", "phase", "qualification", "qualifying", "round",
    "rodada", "regular", "quarter", "quarterfinals", "semifinals", "final",
    "oitavas", "quartas", "semifinal", "playoff", "playoffs",
}


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).split())


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _alias_kind(name: str, country: str = "") -> str | None:
    normalized = normalize_name(name)
    normalized_country = normalize_name(country)
    if normalized == "serie a" and normalized_country == "brazil":
        return "brasileirao_serie_a"
    for kind, aliases in COMPETITION_ALIASES.items():
        for alias in aliases:
            if normalized == alias:
                return kind
            suffix = normalized.removeprefix(alias).strip()
            if suffix and normalized.startswith(alias + " ") and suffix.split()[0] in _STAGE_WORDS:
                return kind
    return None


def competition_kind(league_id: Any, name: str, country: str = "") -> str | None:
    stable_id = _as_int(league_id)
    return COMPETITION_IDS.get(stable_id) or _alias_kind(name, country)


@dataclass(frozen=True)
class BrasileiraoMembership:
    season: int
    team_ids: frozenset[int]
    team_names: frozenset[str]
    source: str = ""

    @classmethod
    def empty(cls, season: int) -> "BrasileiraoMembership":
        return cls(season, frozenset(), frozenset(), "unavailable")

    @classmethod
    def from_api_response(
        cls, season: int, payload: dict[str, Any], source: str = "api-football"
    ) -> "BrasileiraoMembership":
        teams = [row.get("team") or {} for row in payload.get("response") or [] if isinstance(row, dict)]
        return cls(
            season,
            frozenset(team_id for team in teams if (team_id := _as_int(team.get("id"))) is not None),
            frozenset(normalize_name(team.get("name", "")) for team in teams if team.get("name")),
            source,
        )

    def contains(self, team_id: Any, team_name: str) -> bool:
        stable_id = _as_int(team_id)
        return (stable_id is not None and stable_id in self.team_ids) or normalize_name(team_name) in self.team_names

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "league_id": BRASILEIRAO_SERIE_A_ID,
            "team_ids": sorted(self.team_ids),
            "team_names": sorted(self.team_names),
            "source": self.source,
        }


def membership_from_fixtures(season: int, fixtures: Iterable[Any]) -> BrasileiraoMembership:
    ids: set[int] = set()
    names: set[str] = set()
    for fixture in fixtures:
        if competition_kind(getattr(fixture, "league_id", None), fixture.competition, getattr(fixture, "league_country", "")) != "brasileirao_serie_a":
            continue
        for side in ("home", "away"):
            team_id = _as_int(getattr(fixture, f"{side}_team_id", None))
            if team_id is not None:
                ids.add(team_id)
            names.add(normalize_name(getattr(fixture, f"{side}_team", "")))
    return BrasileiraoMembership(season, frozenset(ids), frozenset(filter(None, names)), "fixture-metadata")
