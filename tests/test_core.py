import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jaguartv_prematch.collector import tomorrow_brasilia
from jaguartv_prematch.config import FactoryConfig
from jaguartv_prematch.pipeline import run_phase1, run_phase3
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


def test_middle_slots_use_distinct_operation_assets():
    pools = {
        "operation": ["op-a", "op-b"],
        "interface": ["op-a", "op-b"],
        "cta": ["cta"],
        "music": ["music"],
        "voice": ["voice"],
    }
    selected = deterministic_batch_rotation("2026-09-03", ["fixture"], pools)
    item = selected["fixture"]
    assert item["operation"] != item["interface"]


def test_middle_visual_pairs_rotate_before_audio_only_changes():
    pools = {
        "operation": ["op-a", "op-b", "op-c"],
        "interface": ["op-a", "op-b", "op-c"],
        "cta": ["cta"],
        "music": ["music"],
        "voice": ["voice-a", "voice-b"],
    }
    selected = deterministic_batch_rotation("2026-09-03", ["a", "b", "c"], pools)
    pairs = {(item["operation"], item["interface"]) for item in selected.values()}
    assert len(pairs) == 3


def test_upload_url_rejects_embedded_credentials():
    with pytest.raises(ValueError):
        _validated_base_url("https://user:password@example.com")


def test_phase1_uses_manual_fixture_file_when_collector_fails(tmp_path):
    manual = tmp_path / "fixtures.json"
    manual.write_text(json.dumps({"fixtures": [fixture(competition="Campeonato Brasileiro Série A", home_team="Santos").to_dict()]}, ensure_ascii=False), encoding="utf-8")
    config = FactoryConfig(Path("test"), {
        "collector": {"base_url": "http://127.0.0.1:1/api/v1/fixtures", "timeout_seconds": 1},
        "text": {"provider_id": "deepseek", "primary_model_id": "deepseek-v4-flash", "fallback_model_id": "deepseek-v4-pro"},
        "image": {"model_id": "gpt-image-2", "primary": {}, "fallback": {}},
        "video": {"provider_id": "operator-dreamina-vip", "model_id": "seedance2.0fast_vip"},
        "publishing": {"inventory_label": "赛前预测"},
    })
    result = run_phase1(config, tmp_path / "run", manual)
    selected = json.loads((tmp_path / "run" / "phase1" / "selected-fixtures.json").read_text(encoding="utf-8"))
    assert result["source"] == "manual_fixture_file"
    assert len(selected["fixtures"]) == 1


def test_phase3_dry_run_creates_4x5_posters(tmp_path):
    phase1 = tmp_path / "run" / "phase1"
    phase1.mkdir(parents=True)
    phase1.joinpath("selected-fixtures.json").write_text(
        json.dumps({"fixtures": [{"selection_reason": "target_club", **fixture(home_team="Santos", away_team="Internacional").to_dict()}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    config = FactoryConfig(Path("test"), {
        "collector": {"base_url": "http://127.0.0.1:1/api/v1/fixtures"},
        "text": {"provider_id": "deepseek", "primary_model_id": "deepseek-v4-flash", "fallback_model_id": "deepseek-v4-pro"},
        "image": {"model_id": "gpt-image-2", "size": "1024x1280", "primary": {}, "fallback": {}},
        "video": {"provider_id": "operator-dreamina-vip", "model_id": "seedance2.0fast_vip"},
        "publishing": {"inventory_label": "赛前预测"},
    })
    result = run_phase3(config, tmp_path / "run", dry_run=True)
    assert result["poster_count"] == 2
    assert result["items"][0]["fixed_logo_overlay"]["logo_sha256"]
    from PIL import Image
    with Image.open(result["items"][0]["poster"]) as poster:
        assert poster.size == (1024, 1280)
