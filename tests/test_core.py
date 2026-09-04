import json
import http.client
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jaguartv_prematch.collector import tomorrow_brasilia
from jaguartv_prematch.config import FactoryConfig
from jaguartv_prematch.image2 import _download_image_url
from jaguartv_prematch.pipeline import _caption_for, run_phase1, run_phase3
from jaguartv_prematch.records import Fixture
from jaguartv_prematch.routing import CodexDeepSeekRouter, ProviderRoutingError
from jaguartv_prematch.selection import selection_reason
from jaguartv_prematch.upload import _validated_base_url
from jaguartv_prematch.video import deterministic_batch_rotation, video_filenames

import importlib.util


_TASK1_DRIVER = Path(__file__).resolve().parents[1] / "scripts" / "task1_driver.py"
_spec = importlib.util.spec_from_file_location("task1_driver", _TASK1_DRIVER)
task1_driver = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(task1_driver)


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


def test_video_filenames_use_manifest_sequence_prefix():
    names = video_filenames("桑托斯_vs_帕尔梅拉斯_260923_海报.png", 4, 2)
    assert names["media_stem"].startswith("02桑托斯_vs_帕尔梅拉斯_260923_海报")
    assert names["final"].startswith("final-02桑托斯_vs_帕尔梅拉斯_260923_海报")


def test_auto_batch_selects_next_after_completed_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(task1_driver, "REPO", tmp_path)
    (tmp_path / "runs" / "20260905_batch1").mkdir(parents=True)
    (tmp_path / "runs" / "20260905_batch1" / "automation-summary.json").write_text(
        json.dumps({"upload": "PHASE5_COMPLETE"}),
        encoding="utf-8",
    )
    (tmp_path / "runs" / "20260905_batch2").mkdir(parents=True)
    (tmp_path / "runs" / "20260905_batch2" / "automation-summary.json").write_text(
        json.dumps({"upload": "PHASE5_COMPLETE"}),
        encoding="utf-8",
    )

    assert task1_driver._detect_batch("20260905") == 3


def test_auto_batch_ignores_incomplete_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(task1_driver, "REPO", tmp_path)
    (tmp_path / "runs" / "20260905_batch1").mkdir(parents=True)
    (tmp_path / "runs" / "20260905_batch1" / "automation-summary.json").write_text(
        json.dumps({"upload": "PHASE5_COMPLETE"}),
        encoding="utf-8",
    )
    (tmp_path / "runs" / "20260905_batch2").mkdir(parents=True)
    (tmp_path / "runs" / "20260905_batch2" / "automation-summary.json").write_text(
        json.dumps({"upload": "DRY_RUN_NOT_UPLOADED"}),
        encoding="utf-8",
    )

    assert task1_driver._detect_batch("20260905") == 2


def test_same_day_styles_do_not_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(task1_driver, "REPO", tmp_path)
    prior = tmp_path / "runs" / "20260905_batch1" / "phase3"
    prior.mkdir(parents=True)
    prior.joinpath("style-selection.json").write_text(
        json.dumps({"selected_style": "neon_editorial"}),
        encoding="utf-8",
    )
    hist = {"pool": ["neon_editorial", "broadcast_green_gold"], "recent_styles": []}

    selected = task1_driver._select_style(hist, 2, "20260905")

    assert selected["selected_style"] == "broadcast_green_gold"
    assert "neon_editorial" in selected["excluded_same_day_styles"]


def test_same_day_styles_fail_when_pool_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(task1_driver, "REPO", tmp_path)
    for batch, style in [(1, "a"), (2, "b")]:
        phase3 = tmp_path / "runs" / f"20260905_batch{batch}" / "phase3"
        phase3.mkdir(parents=True)
        phase3.joinpath("style-selection.json").write_text(
            json.dumps({"selected_style": style}),
            encoding="utf-8",
        )
    hist = {"pool": ["a", "b"], "recent_styles": []}

    with pytest.raises(RuntimeError, match="no unused poster style"):
        task1_driver._select_style(hist, 3, "20260905")


def test_captions_change_between_batches(tmp_path):
    run_dir = tmp_path / "20260905_batch1"
    phase1 = run_dir / "phase1"
    phase2 = run_dir / "phase2"
    phase1.mkdir(parents=True)
    phase2.mkdir(parents=True)
    fx = fixture(
        fixture_id="fixture-1",
        competition="Campeonato Brasileiro Série A",
        home_team="Santos",
        away_team="Palmeiras",
        schedule_date="2026-09-05",
        kickoff_at_brt="20:30",
    ).to_dict()
    phase1.joinpath("selected-fixtures.json").write_text(json.dumps({"fixtures": [fx]}, ensure_ascii=False), encoding="utf-8")
    phase2.joinpath("fixture-1_research.json").write_text(
        json.dumps({"projected_or_inferred": [{"claim": "Editorial prediction: Santos 1 x 2 Palmeiras"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    items = [{"task_id": "fixture-1", "kind": "single"}]

    batch1 = task1_driver._captions_for_batch(run_dir, items, 1)["items"][0]["description"]
    batch2 = task1_driver._captions_for_batch(run_dir, items, 2)["items"][0]["description"]

    assert batch1 != batch2
    assert "Santos x Palmeiras" in batch1
    assert "Santos x Palmeiras" in batch2


def test_caption_has_exactly_five_hashtags_with_marketing(tmp_path):
    run_dir = tmp_path / "run"
    phase1 = run_dir / "phase1"
    phase2 = run_dir / "phase2"
    phase1.mkdir(parents=True)
    phase2.mkdir(parents=True)
    fx = fixture(competition="Campeonato Brasileiro Série A", home_team="Santos", away_team="Palmeiras").to_dict()
    phase1.joinpath("selected-fixtures.json").write_text(json.dumps({"fixtures": [fx]}, ensure_ascii=False), encoding="utf-8")
    phase2.joinpath("fixture-1_research.json").write_text(json.dumps({"projected_or_inferred": [{"claim": "Editorial prediction: Santos 1 x 2 Palmeiras"}]}, ensure_ascii=False), encoding="utf-8")
    caption = _caption_for(run_dir, {"task_id": "fixture-1"}, [fx], "2026-09-03")
    hashtags = caption["hashtags"]
    assert len(hashtags) == 5
    assert hashtags[-1] == "#jaguartvbrasil"
    assert "Jaguar TV" in caption["description"]
    assert "jaguartvbrasil.com" in caption["description"]


def test_upload_url_rejects_embedded_credentials():
    with pytest.raises(ValueError):
        _validated_base_url("https://user:password@example.com")


def test_image_url_download_retries_incomplete_read(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"png"

    calls = {"count": 0}

    def fake_urlopen(url, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise http.client.IncompleteRead(b"partial")
        return Response()

    monkeypatch.setattr("jaguartv_prematch.image2.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("jaguartv_prematch.image2.time.sleep", lambda _: None)

    assert _download_image_url("https://example.invalid/image.png", 1) == b"png"
    assert calls["count"] == 2


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
