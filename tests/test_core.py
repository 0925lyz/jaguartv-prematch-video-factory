import json
import http.client
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jaguartv_prematch.collector import collect_fixtures, resolve_target_date, tomorrow_brasilia
from jaguartv_prematch.competition import BrasileiraoMembership, competition_kind
from jaguartv_prematch.config import FactoryConfig
from jaguartv_prematch.image2 import _download_image_url, _verify_requested_size
from jaguartv_prematch.pipeline import _caption_for, _predicted_score, _schedule_prompt_tasks, run_phase1, run_phase3
from jaguartv_prematch.poster import _transparent_icon
from jaguartv_prematch.records import Fixture
from jaguartv_prematch.routing import CodexTextRouter, ProviderRoutingError
from jaguartv_prematch.retry import retry_forever
from jaguartv_prematch.selection import selection_reason
from jaguartv_prematch.upload import _validated_base_url
from jaguartv_prematch.video import (
    VideoGenerationError,
    _dreamina_raw_downloads,
    composition_duration,
    deterministic_batch_rotation,
    deterministic_motion_plan,
    generate_apimart_hook,
    submit_dreamina_hook,
    video_filenames,
)

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


def test_target_date_honors_explicit_today():
    now = datetime(2026, 9, 8, 23, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert resolve_target_date("today", now) == "2026-09-08"
    assert resolve_target_date("tomorrow", now) == "2026-09-09"


def test_official_agenda_filters_exact_target_date(monkeypatch):
    payload = {
        "success": True,
        "matches": [
            {
                "id": "old",
                "homeTeam": "Old Home",
                "awayTeam": "Old Away",
                "date": "08/09",
                "time": "19:00",
                "league": "Premier League",
                "channel": "Jaguar TV",
                "isVisible": True,
            },
            {
                "id": "target",
                "homeTeam": "Barcelona",
                "awayTeam": "Valencia",
                "date": "09/09",
                "time": "21:30",
                "league": "La Liga",
                "channels": ["Jaguar TV 1", "ESPN"],
                "isVisible": True,
            },
        ],
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr("jaguartv_prematch.collector.urllib.request.urlopen", lambda request, timeout: Response())

    fixtures, _ = collect_fixtures("https://copa.jarg.top/api/save-agenda?dedup=true", date="20260909")

    assert [fixture.fixture_id for fixture in fixtures] == ["target"]
    assert fixtures[0].schedule_date == "2026-09-09"
    assert fixtures[0].home_team == "Barcelona"
    assert fixtures[0].channels == ("Jaguar TV 1", "ESPN")
    assert fixtures[0].featured is True


def test_driver_passes_run_date_to_collect(monkeypatch, tmp_path):
    run_dir = tmp_path / "runs" / "20260909_batch1"
    phase1 = run_dir / "phase1"
    phase1.mkdir(parents=True)
    phase1.joinpath("selected-fixtures.json").write_text(
        json.dumps({"fixtures": [fixture(schedule_date="2026-09-09").to_dict()]}),
        encoding="utf-8",
    )
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(task1_driver, "_run", fake_run)
    config = SimpleNamespace(path=tmp_path / "config.json")

    task1_driver._phase1(config, run_dir, False, False)

    assert seen["cmd"][seen["cmd"].index("--date") + 1] == "2026-09-09"


def test_major_league_requires_verified_current_brazilian_player():
    assert selection_reason(fixture()) is None
    selected = fixture(verified_brazilian_players=("Jogador Atual",))
    assert selection_reason(selected) == "verified_current_brazilian_player"


def test_target_club_is_selected():
    assert selection_reason(fixture(home_team="Manchester City")) == "target_club"


def test_high_value_competitions_are_selected_by_api_football_names():
    assert selection_reason(fixture(competition="UEFA Champions League - League Stage - 1")) == "target_competition"
    assert selection_reason(fixture(competition="CONMEBOL Libertadores - Quarter-finals")) == "target_competition"
    assert selection_reason(fixture(competition="CONMEBOL Sudamericana - Quarter-finals")) == "target_competition"


def test_conmebol_competitions_prefer_ids_and_accept_normalized_aliases():
    assert competition_kind(13, "odd upstream label") == "copa_libertadores"
    assert competition_kind(11, "odd upstream label") == "copa_sudamericana"
    assert competition_kind(None, "Taça Libertadores da América - Fase de grupos") == "copa_libertadores"
    assert competition_kind(None, "Copa Sul-Americana - Quartas") == "copa_sudamericana"
    assert competition_kind(None, "Serie A - Regular Season - 4", "Italy") is None


def test_unrelated_competitions_still_fall_through():
    assert selection_reason(fixture(competition="Liga Pro Serie B - Promotion Group - 3")) is None
    assert selection_reason(fixture(competition="Capixaba B - Semi-finals")) is None
    assert selection_reason(fixture(competition="2. Bundesliga - Regular Season - 5")) is None


def test_non_featured_match_is_rejected():
    assert selection_reason(fixture(featured=False, home_team="Manchester City")) is None


def test_brasileirao_match_without_channel_data_is_still_selected():
    # The official agenda leaves the broadcast column empty for this round, which makes
    # the derived `featured` flag False. An incomplete channel cell must not drop a whole
    # Brazilian top-flight fixture.
    coritiba = fixture(
        competition="Serie A - Regular Season - 27",
        home_team="Coritiba",
        away_team="Atletico Paranaense",
        channels=(),
        featured=False,
    )
    membership = BrasileiraoMembership(2026, frozenset({123}), frozenset({"coritiba"}), "test")
    assert selection_reason(coritiba, membership) == "brasileirao_team"


def test_italian_serie_a_without_channel_data_is_not_selected():
    # "Serie A" is also the Italian top flight; a Brazilian-club check keeps it out.
    italian = fixture(
        competition="Serie A - Regular Season - 4",
        home_team="Venezia",
        away_team="Fiorentina",
        channels=(),
        featured=False,
    )
    assert selection_reason(italian) is None


def test_italian_serie_b_without_channel_data_is_not_selected():
    italian = fixture(
        competition="Serie B - Regular Season - 4",
        home_team="Empoli",
        away_team="Arezzo",
        channels=(),
        featured=False,
    )
    assert selection_reason(italian) is None


def test_unambiguous_target_competition_keeps_its_place_without_channel_data():
    copa = fixture(
        competition="Copa do Brasil - Round of 16",
        home_team="Capixaba FC",
        away_team="Vila FC",
        channels=(),
        featured=False,
    )
    assert selection_reason(copa) == "target_competition"


def test_brazilian_club_check_uses_normalized_names():
    accented = fixture(
        competition="Serie A - Regular Season - 27",
        home_team="Grêmio",
        away_team="São Paulo",
        channels=(),
        featured=False,
    )
    membership = BrasileiraoMembership(2026, frozenset(), frozenset({"gremio"}), "test")
    assert selection_reason(accented, membership) == "brasileirao_team"


def test_one_current_brasileirao_team_selects_any_competition_by_stable_id():
    membership = BrasileiraoMembership(2026, frozenset({42}), frozenset(), "test")
    away_member = fixture(
        competition="Mundial de Clubes",
        home_team_id=7,
        away_team_id=42,
        featured=False,
    )
    assert selection_reason(away_member, membership) == "brasileirao_team"


def test_router_rejects_empty_model_route():
    router = CodexTextRouter()
    with pytest.raises(ProviderRoutingError):
        router.verify("")


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
    assert names["hook"].endswith("-4s.mp4")
    assert "12s" not in names["final"]


def test_dreamina_download_selection_ignores_hook_and_final_outputs(tmp_path):
    hook = tmp_path / "hook-05260908_赛程海报-3s.mp4"
    final = tmp_path / "final-05260908_赛程海报-12s.mp4"
    raw = tmp_path / "0f53000a-1957-4439-a4ea-6ccfe4c68981_video_1.mp4"
    for path in (hook, final, raw):
        path.write_bytes(b"mp4")

    selected = _dreamina_raw_downloads(tmp_path, "0f53000a-1957-4439-a4ea-6ccfe4c68981", set())

    assert selected == [raw]


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


def test_auto_batch_fills_missing_lower_batch_before_appending(tmp_path, monkeypatch):
    monkeypatch.setattr(task1_driver, "REPO", tmp_path)
    for batch in (1, 3):
        run_dir = tmp_path / "runs" / f"20260905_batch{batch}"
        run_dir.mkdir(parents=True)
        run_dir.joinpath("automation-summary.json").write_text(
            json.dumps({"upload": "PHASE5_COMPLETE"}),
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
    assert "Acesse jaguartvbrasil.com/baixar-app para baixar." in batch1
    assert "#jaguartv" in batch1
    assert "#iptv" in batch1


def test_predicted_score_line_keeps_the_away_team(tmp_path):
    run_dir = tmp_path / "20260911_batch9"
    phase2 = run_dir / "phase2"
    phase2.mkdir(parents=True)
    phase2.joinpath("fixture-1_research.json").write_text(
        json.dumps({"projected_or_inferred": [{"claim_pt": "Coritiba 1 x 1 Athletico Paranaense"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    assert _predicted_score(run_dir, "fixture-1") == "Coritiba 1 x 1 Athletico Paranaense"


def test_predicted_score_line_tolerates_a_claim_without_the_away_team(tmp_path):
    run_dir = tmp_path / "20260911_batch9"
    phase2 = run_dir / "phase2"
    phase2.mkdir(parents=True)
    phase2.joinpath("fixture-1_research.json").write_text(
        json.dumps({"projected_or_inferred": [{"claim": "Editorial prediction: Santos 1 x 2"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    assert _predicted_score(run_dir, "fixture-1") == "Santos 1 x 2"


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
    assert "#jaguartv" in hashtags
    assert "#iptv" in hashtags
    assert "Jaguar TV" in caption["description"]
    assert "Acesse jaguartvbrasil.com/baixar-app para baixar." in caption["description"]


def test_composition_duration_uses_full_video_lengths(monkeypatch):
    durations = {"operation.mp4": 3.0, "interface.mp4": 6.0, "cta.mp4": 4.5}
    monkeypatch.setattr("jaguartv_prematch.video.media_duration", lambda path: durations[Path(path).name])
    components = {"operation": "operation.mp4", "interface": "interface.mp4", "cta": "cta.mp4"}
    assert composition_duration(components) == 17.5


def test_missing_dreamina_command_becomes_provider_error(monkeypatch, tmp_path):
    monkeypatch.setattr("jaguartv_prematch.video.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("dreamina")))
    with pytest.raises(VideoGenerationError):
        submit_dreamina_hook(tmp_path / "master.png", "prompt", {"model_id": "seedance", "generation_seconds": 4})


def test_apimart_hook_uses_required_model_parameters(monkeypatch, tmp_path):
    class Response:
        def __init__(self, payload=None, content=b"", status=200):
            self.payload = payload or {}
            self.content = content
            self.status_code = status
            self.ok = 200 <= status < 300

        def json(self):
            return self.payload

    submitted = {}

    def fake_post(url, **kwargs):
        if url.endswith("/uploads/images"):
            return Response({"url": "https://upload.example/master.png"})
        submitted.update(kwargs["json"])
        return Response({"data": [{"task_id": "task-1"}]})

    def fake_get(url, **kwargs):
        if "/tasks/" in url:
            return Response({"data": {"status": "completed", "result": {"videos": [{"url": ["https://cdn.example/hook.mp4"]}]}}})
        return Response(content=b"0" * 20_001)

    monkeypatch.setattr("jaguartv_prematch.video.resolve_secret", lambda route: "secret")
    monkeypatch.setattr("jaguartv_prematch.video.requests.post", fake_post)
    monkeypatch.setattr("jaguartv_prematch.video.requests.get", fake_get)
    master = tmp_path / "master.png"
    master.write_bytes(b"png")
    output = tmp_path / "hook.mp4"
    route = {
        "provider_id": "apimart",
        "model_id": "wan2.6-i2v-flash",
        "base_url": "https://api.apimart.ai/v1",
        "resolution": "720p",
        "generation_seconds": 4,
    }

    result = generate_apimart_hook(master, "animate", route, output)

    assert output.stat().st_size == 20_001
    assert submitted == {
        "model": "wan2.6-i2v-flash",
        "prompt": "animate",
        "image_urls": ["https://upload.example/master.png"],
        "resolution": "720p",
        "duration": 4,
    }
    assert result["provider_id"] == "apimart"


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
        "text": {"provider_id": "current-task", "primary_model_id": "current-task", "fallback_model_id": "current-task"},
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
        "text": {"provider_id": "current-task", "primary_model_id": "current-task", "fallback_model_id": "current-task"},
        "image": {"model_id": "gpt-image-2", "size": "1024x1280", "primary": {}, "fallback": {}},
        "video": {"provider_id": "operator-dreamina-vip", "model_id": "seedance2.0fast_vip"},
        "publishing": {"inventory_label": "赛前预测"},
    })
    result = run_phase3(config, tmp_path / "run", dry_run=True)
    assert result["poster_count"] == 2
    assert result["items"][0]["compose"]["logo_sha256"]
    assert result["items"][0]["compose"]["all_content_in_bounds"] is True
    from PIL import Image
    with Image.open(result["items"][0]["poster"]) as poster:
        assert poster.size == (2048, 2560)
    with Image.open(result["items"][0]["foreground"]) as foreground:
        assert foreground.size == (2048, 2560)
        assert foreground.mode == "RGBA"
        assert foreground.getchannel("A").getbbox()


def test_schedule_pages_are_capped_at_eight_matches():
    fixtures = [fixture(fixture_id=f"f-{index}").to_dict() for index in range(9)]
    pages = _schedule_prompt_tasks(fixtures)
    assert [len(page["fixtures"]) for page in pages] == [8, 1]
    assert len({page["id"] for page in pages}) == 2


def test_channel_icon_edge_matte_is_transparent_without_erasing_center(tmp_path):
    from PIL import Image, ImageDraw

    source = tmp_path / "channel.png"
    icon = Image.new("RGBA", (80, 50), (0, 0, 0, 255))
    ImageDraw.Draw(icon).rectangle((20, 12, 60, 38), fill=(255, 255, 255, 255))
    icon.save(source)
    cleaned = _transparent_icon(source, (80, 50))
    assert cleaned.getpixel((0, 0))[3] == 255
    assert cleaned.width < 80 and cleaned.height < 50


def test_image_size_guard_rejects_wrong_aspect_ratio(tmp_path):
    from PIL import Image

    poster = tmp_path / "wrong.png"
    Image.new("RGB", (793, 1983), (0, 0, 0)).save(poster)

    with pytest.raises(RuntimeError, match="793x1983"):
        _verify_requested_size(poster, "1024x1280")


def test_image_size_guard_accepts_4x5_canvases(tmp_path):
    from PIL import Image

    for size in ((1024, 1280), (1122, 1402)):
        poster = tmp_path / f"ok-{size[0]}.png"
        Image.new("RGB", size, (0, 0, 0)).save(poster)
        _verify_requested_size(poster, "1024x1280")


def test_motion_plan_is_reproducible_and_calls_video_for_exactly_half():
    even = deterministic_motion_plan(["a", "b", "c", "d"], "2026-09-15")
    odd = deterministic_motion_plan(["a", "b", "c", "d", "e"], "2026-09-15")
    assert even == deterministic_motion_plan(["a", "b", "c", "d"], "2026-09-15")
    assert sum(item["video_model_called"] for item in even.values()) == 2
    assert sum(item["video_model_called"] for item in odd.values()) == 2
    assert sum(item["reason"] == "odd_batch_candidate_dropped_to_static" for item in odd.values()) == 1


def test_apimart_retry_persists_transient_failure_and_stops_on_auth(tmp_path):
    state = tmp_path / "retry.json"
    calls = []

    def transient_then_success():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("HTTP 503 temporary service error")
        return "ok"

    assert retry_forever(
        transient_then_success, state_path=state, operation_name="test", base_delay=0,
        sleeper=lambda _delay: None,
    ) == "ok"
    assert json.loads(state.read_text(encoding="utf-8"))["status"] == "succeeded"
    with pytest.raises(RuntimeError, match="HTTP 401"):
        retry_forever(
            lambda: (_ for _ in ()).throw(RuntimeError("HTTP 401 invalid API key")),
            state_path=tmp_path / "auth.json", operation_name="auth", base_delay=0,
            sleeper=lambda _delay: None,
        )
    resume = tmp_path / "resume.json"
    resume.write_text(json.dumps({
        "status": "retry_wait", "attempt": 4, "next_retry_at": "2099-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    resumed_delays = []
    assert retry_forever(
        lambda: "resumed", state_path=resume, operation_name="resume",
        sleeper=resumed_delays.append,
    ) == "resumed"
    assert resumed_delays and resumed_delays[0] > 0


def _fake_lark_proc(message_id: str):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"data": {"message_id": message_id}}),
        stderr="",
    )


def test_lark_sample_is_posted_to_the_group_at_most_once(tmp_path, monkeypatch):
    """Any replay of a run must not post a second copy of the same sample to the group."""
    run_dir = tmp_path / "20260911_batch1"
    media_dir = run_dir / "phase4" / "01Example FC_vs_Other FC_260911_海报"
    media_dir.mkdir(parents=True)
    final = media_dir / "final-01Example FC_vs_Other FC_260911_海报.mp4"
    final.write_bytes(b"video-bytes")
    (run_dir / "phase4" / "build-manifest.json").write_text(
        json.dumps({"items": [{"task_id": "fixture-1", "kind": "single", "final": str(final)}]}),
        encoding="utf-8",
    )
    captions = {"items": [{"task_id": "fixture-1", "description": "palpite do dia"}]}

    commands = []

    def fake_send(command, **kwargs):
        commands.append(command)
        return _fake_lark_proc(f"om_{len(commands)}")

    monkeypatch.setattr(task1_driver, "_lark_send", fake_send)

    first = task1_driver._lark_sample(run_dir, captions, False)
    assert first["file_sent"] is True and first["text_sent"] is True
    assert len(commands) == 2  # one video message + one text message

    replayed = task1_driver._lark_sample(run_dir, captions, False)
    assert replayed == first
    assert len(commands) == 2  # replay is a no-op, the group gets nothing new

    forced = task1_driver._lark_sample(run_dir, captions, False, force=True)
    assert len(commands) == 4  # --lark-only still re-sends on purpose
    assert forced["video_msg_id"] != first["video_msg_id"]
