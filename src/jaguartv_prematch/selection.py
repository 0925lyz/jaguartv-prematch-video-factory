from __future__ import annotations

from .competition import BrasileiraoMembership, competition_kind, normalize_name
from .records import Fixture


TARGET_COMPETITION_ALIASES = {
    "campeonato brasileiro serie b",
    "brasileirao serie b",
    "copa do brasil",
    "uefa champions league",
}

TARGET_CLUBS = {
    "boca juniors", "river plate", "benfica", "porto", "sporting cp", "sporting",
    "fenerbahce", "manchester city", "chelsea", "arsenal", "manchester united",
    "liverpool", "tottenham", "nottingham forest", "newcastle united", "everton",
    "real madrid", "atletico de madrid", "barcelona", "juventus", "napoli", "roma",
    "inter milan", "internazionale", "ac milan", "milan", "borussia dortmund",
    "bayern munich", "bayern munchen", "paris saint germain", "psg",
}

_STAGE_WORDS = {"group", "league", "stage", "fase", "qualification", "round", "rodada", "regular", "quarter", "semifinal", "final"}


def _named_target(competition: str) -> bool:
    normalized = normalize_name(competition)
    for alias in TARGET_COMPETITION_ALIASES:
        if normalized == alias:
            return True
        suffix = normalized.removeprefix(alias).strip()
        if suffix and normalized.startswith(alias + " ") and suffix.split()[0] in _STAGE_WORDS:
            return True
    return False


def selection_reason(
    fixture: Fixture, membership: BrasileiraoMembership | None = None
) -> str | None:
    membership = membership or BrasileiraoMembership.empty(fixture.league_season or 0)
    if membership.contains(fixture.home_team_id, fixture.home_team) or membership.contains(
        fixture.away_team_id, fixture.away_team
    ):
        return "brasileirao_team"
    if competition_kind(fixture.league_id, fixture.competition, fixture.league_country):
        return "target_competition"
    if _named_target(fixture.competition):
        return "target_competition"
    if not fixture.featured:
        return None
    teams = {normalize_name(fixture.home_team), normalize_name(fixture.away_team)}
    if teams & TARGET_CLUBS:
        return "target_club"
    if fixture.verified_brazilian_players:
        return "verified_current_brazilian_player"
    return None


def select_fixtures(
    fixtures: list[Fixture], membership: BrasileiraoMembership | None = None
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for fixture in fixtures:
        reason = selection_reason(fixture, membership)
        if reason:
            selected.append({"selection_reason": reason, **fixture.to_dict()})
    return selected
