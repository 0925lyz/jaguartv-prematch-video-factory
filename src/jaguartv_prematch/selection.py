from __future__ import annotations

import re
import unicodedata

from .records import Fixture


TARGET_COMPETITIONS = {
    "campeonato brasileiro serie a",
    "campeonato brasileiro serie b",
    "brasileirao serie a",
    "brasileirao serie b",
    # API-Football names the Brazilian top flights "Serie A"/"Serie B" in this feed, and the
    # continental cups arrive as "CONMEBOL ...". These are matched as prefixes below so the
    # " - Regular Season - 27" / " - Quarter-finals" suffixes do not defeat the lookup.
    "serie a",
    "serie b",
    "copa do brasil",
    "conmebol libertadores",
    "conmebol sudamericana",
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

# The official agenda labels BOTH the Italian and the Brazilian top two divisions simply
# "Serie A"/"Serie B" (e.g. "Serie A - Regular Season - 4" is Italian, "Serie A - Regular
# Season - 27" is Brasileirao). Those two names therefore carry no nationality on their own
# and are the only ambiguous entries in TARGET_COMPETITIONS.
AMBIGUOUS_TARGET_COMPETITIONS = {"serie a", "serie b"}

# Brazilian clubs (top flight plus the traditional second-division sides). Used ONLY to tell
# an Italian "Serie A"/"Serie B" fixture apart from a Brazilian one when deciding whether a
# missing broadcast-channel column may disqualify a target-competition match -- see
# selection_reason. It never selects anything by itself.
BRAZILIAN_CLUBS = {
    "abc", "america mineiro", "america-mg", "amazonas", "athletic-mg",
    "athletico paranaense", "atletico paranaense",
    "atletico goianiense", "atletico-go", "atletico mg", "atletico-mg", "atletico mineiro",
    "avai", "bahia", "botafogo", "botafogo pb", "botafogo-sp", "botafogo sp",
    "bragantino", "red bull bragantino", "brusque", "caxias", "ceara", "chapecoense",
    "corinthians", "coritiba", "crb", "criciuma", "cruzeiro", "csa", "cuiaba",
    "ferroviaria", "figueirense", "flamengo", "fluminense", "fortaleza", "goias",
    "gremio", "guarani", "internacional", "ituano", "juventude", "londrina", "mirassol",
    "nautico", "novorizontino", "operario", "palmeiras", "paysandu", "ponte preta",
    "remo", "sampaio correia", "santos", "sao bernardo", "sao paulo", "sport",
    "sport recife", "tombense", "vasco", "vasco da gama", "vila nova", "vitoria",
    "volta redonda", "ypiranga",
}

MAJOR_LEAGUES = {
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _matched_target_competitions(competition: str) -> set[str]:
    return {
        target
        for target in TARGET_COMPETITIONS
        if competition == target or competition.startswith(f"{target} ")
    }


def _is_brazilian_fixture(teams: set[str]) -> bool:
    return bool(teams & BRAZILIAN_CLUBS)


def _channel_data_gap_is_acceptable(competition: str, teams: set[str]) -> bool:
    """Whether a target-competition fixture may be selected without the `featured` flag.

    `featured` is derived mostly from the agenda's broadcast-channel column, and that column is
    regularly empty for fixtures the agenda has not assigned a broadcaster to yet -- including
    Brasileirao matches, which is how a whole Brazilian top-flight round was being dropped.
    An empty channel cell is a data gap, not a relevance signal, so a target competition keeps
    its place. The Italian/Brazilian "Serie A"/"Serie B" collision is the one case where the
    competition name alone cannot say whether the fixture is Brazilian content, so it needs a
    Brazilian club on the pitch first.
    """
    matched = _matched_target_competitions(competition)
    if not matched:
        return False
    if matched <= AMBIGUOUS_TARGET_COMPETITIONS:
        return _is_brazilian_fixture(teams)
    return True


def selection_reason(fixture: Fixture) -> str | None:
    competition = normalize(fixture.competition)
    teams = {normalize(fixture.home_team), normalize(fixture.away_team)}
    target_competition = bool(_matched_target_competitions(competition))
    if (
        not fixture.featured
        and not _channel_data_gap_is_acceptable(competition, teams)
    ):
        return None
    if target_competition:
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
