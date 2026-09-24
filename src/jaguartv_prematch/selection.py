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
# so the competition name is the only usable signal. Matching stays on the family prefix plus a
# numeric or stage suffix so that neighbouring club families cannot leak in: "UEFA Europa League"
# must not match the "uefa euro" family, and "FIFA Club World Cup" is rejected outright.
NATIONAL_TEAM_COMPETITIONS = {
    "uefa nations league",
    "concacaf nations league",
    "conmebol nations league",
    "uefa euro",
    "euro championship",
    "european championship",
    "conmebol copa america",
    "copa america",
    "fifa world cup",
    "world cup",
    "wc qualification",
    "world cup qualification",
    "concacaf gold cup",
    "gold cup",
    "afc asian cup",
    "africa cup of nations",
    "caf africa cup of nations",
    "international friendlies",
    "friendlies",
}

# A national-team fixture is published only when a Brazilian outlet actually carries it;
# YouTube-only rows are free-to-air filler and stay out of the paid slate. Set to False to
# publish every national-team match regardless of broadcaster.
REQUIRE_BRAZILIAN_BROADCAST_FOR_NATIONAL_TEAMS = True
_NON_BROADCAST_CHANNELS = {"youtube"}


def _national_team_competition(competition: str) -> bool:
    normalized = normalize_name(competition)
    if not normalized or "club" in normalized.split():
        return False
    for alias in NATIONAL_TEAM_COMPETITIONS:
        if normalized == alias:
            return True
        if normalized.startswith(alias + " "):
            suffix = normalized[len(alias):].strip().split()
            if suffix and (suffix[0].isdigit() or suffix[0] in _STAGE_WORDS):
                return True
    return False


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
