from __future__ import annotations

import re
import unicodedata

from .records import Fixture


TARGET_COMPETITIONS = {
    "campeonato brasileiro serie a",
    "campeonato brasileiro serie b",
    "brasileirao serie a",
    "brasileirao serie b",
    "copa do brasil",
}

TARGET_CLUBS = {
    "boca juniors", "river plate", "benfica", "porto", "sporting cp", "sporting",
    "fenerbahce", "manchester city", "chelsea", "arsenal", "manchester united",
    "liverpool", "tottenham", "nottingham forest", "newcastle united", "everton",
    "real madrid", "atletico de madrid", "barcelona", "juventus", "napoli", "roma",
    "inter milan", "internazionale", "ac milan", "milan", "borussia dortmund",
    "bayern munich", "bayern munchen", "paris saint germain", "psg",
}

MAJOR_LEAGUES = {
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def selection_reason(fixture: Fixture) -> str | None:
    if not fixture.featured:
        return None
    competition = normalize(fixture.competition)
    teams = {normalize(fixture.home_team), normalize(fixture.away_team)}
    if competition in TARGET_COMPETITIONS:
        return "target_competition"
    if teams & TARGET_CLUBS:
        return "target_club"
    if fixture.verified_brazilian_players:
        return "verified_current_brazilian_player"
    return None


def select_fixtures(fixtures: list[Fixture]) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for fixture in fixtures:
        reason = selection_reason(fixture)
        if reason:
            selected.append({"selection_reason": reason, **fixture.to_dict()})
    return selected
