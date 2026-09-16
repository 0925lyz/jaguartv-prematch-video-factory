from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    competition: str
    home_team: str
    away_team: str
    schedule_date: str
    kickoff_at_brt: str
    channels: tuple[str, ...]
    featured: bool
    source_url: str
    retrieved_at: str
    source_text: str
    league_id: int | None = None
    league_country: str = ""
    league_season: int | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    home_crest_url: str = ""
    away_crest_url: str = ""
    verified_brazilian_players: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderUse:
    provider_id: str
    model_id: str
    started_at: str
    completed_at: str | None
    status: str
    fallback_from: str | None = None
    sanitized_error: str | None = None
