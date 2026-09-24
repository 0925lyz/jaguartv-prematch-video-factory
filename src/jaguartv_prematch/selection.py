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

# National-team competition families. The upstream agenda feed carries neither a stable
# league id nor a country for these rows (league_id is always null, league_country is empty),
# so the competition name is the only usable signal. Families are matched as whole phrases on
# token boundaries so a confederation prefix or a round suffix cannot break the match
# ("CONMEBOL World Cup Qualification", "WC Qualification South America") while neighbouring club
# families stay out: "UEFA Europa League" has the token "europa", never "euro".
NATIONAL_TEAM_COMPETITIONS = (
    "nations league",
    "nations cup",
    "world cup",
    "wc qualification",
    "euro",
    "european championship",
    "copa america",
    "gold cup",
    "asian cup",
    "cup of nations",
    "confederations cup",
    "arab cup",
    "gulf cup",
    "friendlies",
)

# Only senior men's national teams are in scope. Women's and age-group competitions carry the
# same family words ("UEFA Women's Nations League", "World Cup - U20"), so they are excluded by
# an explicit guard rather than by accident of spelling.
_NON_MEN_TEAM_TOKENS = {
    "women", "womens", "woman", "womans", "ladies", "feminine", "feminino", "femenina",
    "femenino", "youth", "junior", "juniors",
}

# Concacaf spells its women's editions with a single-letter prefix, which no token guard catches.
_NON_MEN_TEAM_PHRASES = ("w gold cup", "w championship", "w nations league")

# The feed's channel column is the only broadcast signal. Kept as a switch so the paid slate can
# be narrowed back to Brazilian outlets if the operator ever wants that again.
REQUIRE_BRAZILIAN_BROADCAST_FOR_NATIONAL_TEAMS = False
_NON_BROADCAST_CHANNELS = {"youtube"}


def _is_senior_men_competition(normalized: str) -> bool:
    tokens = normalized.split()
    if _NON_MEN_TEAM_TOKENS.intersection(tokens):
        return False
    padded = f" {normalized} "
    if any(f" {phrase} " in padded for phrase in _NON_MEN_TEAM_PHRASES):
        return False
    # Age-group teams show up as a u15..u23 token, with or without a separating "-".
    return not any(token.startswith("u") and token[1:].isdigit() for token in tokens)


def _national_team_competition(competition: str) -> bool:
    normalized = normalize_name(competition)
    if not normalized or "club" in normalized.split():
        return False
    if not _is_senior_men_competition(normalized):
        return False
    padded = f" {normalized} "
    return any(f" {alias} " in padded for alias in NATIONAL_TEAM_COMPETITIONS)


def _has_brazilian_broadcast(fixture: Fixture) -> bool:
    if not REQUIRE_BRAZILIAN_BROADCAST_FOR_NATIONAL_TEAMS:
        return True
    return any(
        normalize_name(channel) not in _NON_BROADCAST_CHANNELS
        for channel in fixture.channels
        if str(channel).strip()
    )


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
    if _national_team_competition(fixture.competition):
        return "national_team_competition" if _has_brazilian_broadcast(fixture) else None
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
