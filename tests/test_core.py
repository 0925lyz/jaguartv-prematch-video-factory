from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jaguartv_prematch.collector import tomorrow_brasilia
from jaguartv_prematch.records import Fixture
from jaguartv_prematch.routing import CodexDeepSeekRouter, ProviderRoutingError
from jaguartv_prematch.selection import selection_reason
from jaguartv_prematch.upload import _validated_base_url
from jaguartv_prematch.video import deterministic_batch_rotation


def fixture(**overrides):
    values = {
        "fixture_id": "fixture-1",
        "competition": "Premier League",
        "home_team": "Example FC",
        "away_team": "Other FC",
        "schedule_date": "2026-09-03",
        "kickoff_at_brt": "20:30",
        "channels": ("Jaguar TV 1",),
        "featured": True,
        "source_url": "https://example.invalid/fixture-1",
        "retrieved_at": "2026-09-02T00:00:00-03:00",
        "source_text": "Example FC x Other FC",
    }
    values.update(overrides)
    return Fixture(**values)


def test_tomorrow_uses_brasilia_calendar_date():
    now = datetime(2026, 9, 2, 23, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert tomorrow_brasilia(now) == "2026-09-03"


def test_major_league_requires_verified_current_brazilian_player():
    assert selection_reason(fixture()) is None
    selected = fixture(verified_brazilian_players=("Jogador Atual",))
    assert selection_reason(selected) == "verified_current_brazilian_player"


def test_target_club_is_selected():
    assert selection_reason(fixture(home_team="Manchester City")) == "target_club"


def test_non_featured_match_is_rejected():
    assert selection_reason(fixture(featured=False, home_team="Manchester City")) is None


def test_router_rejects_cross_provider_model():
    router = CodexDeepSeekRouter()
    with pytest.raises(ProviderRoutingError):
        router.verify("unsupported-model")


def test_same_day_component_combinations_are_unique():
    pools = {
        "operation": ["downloader", "search"],
        "interface": ["epg"],
        "cta": ["cta-a", "cta-b"],
        "music": ["music-a"],
        "voice": ["voice-a", "voice-b"],
    }
    selected = deterministic_batch_rotation("2026-09-03", ["a", "b", "c", "d"], pools)
    combinations = [tuple(item.values()) for item in selected.values()]
    assert len(combinations) == len(set(combinations))


def test_upload_url_rejects_embedded_credentials():
    with pytest.raises(ValueError):
        _validated_base_url("https://user:password@example.com")
