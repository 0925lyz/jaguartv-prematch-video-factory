from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .api_football import load_brasileirao_membership
from .collector import FixtureCollectionError, collect_fixtures, load_fixture_file, resolve_target_date, save_collection, tomorrow_brasilia
from .competition import membership_from_fixtures
from .config import FactoryConfig
from .image2 import generate_image2, save_image_route_manifest
from .media_inventory import (
    commit_rotation,
    discard_stale_pending,
    discover_inventory,
    inventory_fingerprint,
    reserve_rotation,
)
from .poster import compose_poster, prepare_background
from .selection import select_fixtures
from .upload import upload_pending_review
from .video import (
    VideoGenerationError,
    compose_v7,
    deterministic_motion_plan,
    download_dreamina_result,
    generate_apimart_hook,
    make_exact_hook,
    make_layered_master,
    submit_dreamina_hook,
    validate_av_duration,
    validate_layered_hook,
    video_filenames,
)


ROOT = Path(__file__).resolve().parents[2]
MONTHS_PT = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
MONTHS_PT_FULL = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
TEAM_COLORS = {
    "Grêmio": "blue, black and white tricolor",
    "Gremio": "blue, black and white tricolor",
    "Internacional": "red and white",
    "Náutico": "red, blue and white",
    "Botafogo-SP": "black and white",
    "Flamengo": "red and black",
    "Palmeiras": "green and white",
    "Santos": "black and white",
    "Vasco da Gama": "black and white with a red diagonal sash",
    "Vitória": "red and black",
    "Vitoria": "red and black",
    "Boca Juniors": "blue and yellow",
    "Vélez Sarsfield": "blue and white",
    "Mirassol": "yellow and green",
}

CREST_SAFETY = (
    "Home team name and crest strictly on the LEFT, away team name and crest strictly on the RIGHT, both correct, "
    "prominent and unobstructed. Use anonymous footballers only: silhouettes, back views or cropped figures in the "
    "correct kit colours, never a recognisable real player face, name or shirt number. "
    "All visible text must be correct Brazilian Portuguese with correct accents. "
    "No invented or garbled words, no fake scores, no fake logos and no fake crests anywhere in the artwork. "
    "No betting, odds or gambling content of any kind."
)
ANTI_FIGURE = (
    "Do NOT include any animal figure, animal head, jaguar head, feline face, lion, tiger, "
    "trophy, cup, ribbon-trophy, mascot figurine, abstract emblem, sports trophy illustration, "
    "or any figurative centrepiece at all. The centre of the artwork is reserved exclusively "
    "for the two club crests, the team names, the prediction box and supporting type. "
    "Do NOT write the words 'JaguarTV', 'JAGUARTV', 'FIGURA', 'CANAL 554', or any other channel/brand "
    "wordmark anywhere in the artwork as rendered text — the JaguarTV brand logo and every channel logo "
    "(Prime Video, SporTV, Premiere, Globo, etc.) are baked into the poster after Image2 generation. "
    "Do NOT invent text labels such as 'CANAL 554', 'JAGUARTV channel', 'JaguarTV channel', "
    "any TV-network wordmark or any channel name — those overlays are added programmatically. "
    "The only figurative animal allowed anywhere is the JaguarTV brand logo, and it is baked into "
    "the poster in the upper-right corner; the video compositor must not add another copy."
)
TEAM_ZH = {
    "Arsenal": "阿森纳",
    "Barcelona": "巴塞罗那",
    "Bayern Munich": "拜仁慕尼黑",
    "Benfica": "本菲卡",
    "Boca Juniors": "博卡青年",
    "Chelsea": "切尔西",
    "Everton": "埃弗顿",
    "Fenerbahçe": "费内巴切",
    "Fenerbahce": "费内巴切",
    "Flamengo": "弗拉门戈",
    "Internacional": "巴西国际",
    "Inter Milan": "国际米兰",
    "Liverpool": "利物浦",
    "Manchester City": "曼城",
    "Manchester United": "曼联",
    "Mirassol": "米拉索尔",
    "Napoli": "那不勒斯",
    "Newcastle United": "纽卡斯尔联",
    "Nottingham Forest": "诺丁汉森林",
    "Paris Saint-Germain": "巴黎圣日耳曼",
    "Porto": "波尔图",
    "Real Madrid": "皇家马德里",
    "River Plate": "河床",
    "Roma": "罗马",
    "Santos": "桑托斯",
    "Sporting CP": "葡萄牙体育",
    "Tottenham": "热刺",
}


def run_phase1(config: FactoryConfig, run_dir: Path, fixtures_file: Path | None = None, target_date: str | None = None) -> dict[str, Any]:
    collector = config.data["collector"]
    api_key = config.env_value(collector, "api_key_env", required=False)
    source = "collector"
    try:
        fixtures, raw = collect_fixtures(
            collector["base_url"],
            api_key=api_key,
            date=target_date or "tomorrow",
            timeout_seconds=int(collector.get("timeout_seconds", 30)),
        )
    except FixtureCollectionError as error:
        if not fixtures_file:
            raise RuntimeError(
                "Phase 1 blocked: fixture collector has no usable data and no --fixtures-file was provided; "
                f"{error}"
            ) from error
        fixtures, raw = load_fixture_file(fixtures_file)
        source = "manual_fixture_file"
        raw["collector_error"] = str(error)

    phase_dir = run_dir / "phase1"
    save_collection(fixtures, raw, phase_dir / "fixtures.json")
    target_iso = fixtures[0].schedule_date if fixtures else resolve_target_date(target_date or "tomorrow")
    target_year = datetime.fromisoformat(target_iso).year
    api_config = config.data.get("api_football") or {}
    membership = membership_from_fixtures(target_year, fixtures)
    if api_config:
        api_key = config.env_value(
            api_config, "api_key_env", required=bool(api_config.get("required", False))
        )
        fetched = load_brasileirao_membership(
            season=target_year,
            cache_path=phase_dir / "brasileirao-membership.json",
            api_key=api_key,
            endpoint=str(api_config.get("teams_endpoint") or "https://v3.football.api-sports.io/teams"),
            timeout=int(api_config.get("timeout_seconds", 30)),
        )
        if fetched.team_names:
            membership = fetched
    _write_json(phase_dir / "brasileirao-membership.json", membership.to_dict())
    selected = {
        "date": target_iso,
        "timezone_label": "Horário de Brasília",
        "source": source,
        "brasileirao_membership": membership.to_dict(),
        "fixtures": select_fixtures(fixtures, membership),
    }
    _write_json(phase_dir / "selected-fixtures.json", selected)
    return {"ok": True, "source": source, "collected": len(fixtures), "selected": len(selected["fixtures"])}


def run_phase2(run_dir: Path, *, dry_run: bool = False, research_dir: Path | None = None) -> dict[str, Any]:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    phase_dir = run_dir / "phase2"
    phase_dir.mkdir(parents=True, exist_ok=True)
    if not fixtures:
        report = {"ok": True, "status": "NO_CONTENT", "research_count": 0}
        _write_json(phase_dir / "research-manifest.json", report)
        return report

    records = []
    for fixture in fixtures:
        fixture_id = str(fixture["fixture_id"])
        candidates = [
            *( [research_dir / f"{fixture_id}_research.json"] if research_dir else [] ),
            phase_dir / f"{fixture_id}_research.json",
        ]
        existing = next((path for path in candidates if path.is_file()), None)
        if existing:
            evidence = _read_json(existing)
        elif dry_run:
            evidence = _dry_research(fixture)
            _write_json(phase_dir / f"{fixture_id}_research.json", evidence)
        else:
            raise RuntimeError(
                f"Phase 2 blocked: missing current research evidence for fixture {fixture_id}; "
                "provide --research-dir or rerun with --dry-run for a local bridge test"
            )
        records.append({"fixture_id": fixture_id, "path": str((phase_dir / f"{fixture_id}_research.json").resolve()), "dry_run": dry_run})
    manifest = {"ok": True, "status": "PHASE2_COMPLETE", "research_count": len(records), "records": records}
    _write_json(phase_dir / "research-manifest.json", manifest)
    return manifest


def run_phase3(config: FactoryConfig, run_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    phase_dir = run_dir / "phase3"
    if not fixtures:
        report = {"ok": True, "status": "NO_CONTENT", "poster_count": 0}
        _write_json(phase_dir / "poster-manifest.json", report)
        return report

    tasks = [_single_prompt_task(item, run_dir) for item in fixtures]
    tasks.extend(_schedule_prompt_tasks(fixtures))
    items = []
    for task in tasks:
        prompt_path = phase_dir / "prompts" / f"{task['id']}.txt"
        raw_path = phase_dir / "raw" / f"{task['id']}.png"
        background_path = phase_dir / "backgrounds" / f"{task['id']}.png"
        foreground_path = phase_dir / "foregrounds" / f"{task['id']}.png"
        poster_path = phase_dir / "posters" / task["filename"]
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(task["prompt"], encoding="utf-8")
        if dry_run:
            _make_dry_poster(task, raw_path)
            route = [{"provider_id": "dry-run", "model_id": "none", "status": "not_called"}]
        else:
            attempts = generate_image2(task["prompt"], raw_path, config.data["image"])
            save_image_route_manifest(phase_dir / "image-routes" / f"{task['id']}.json", attempts)
            route = [item.__dict__ for item in attempts]
        prepare_background(raw_path, background_path)
        task_fixtures = task["fixtures"] if task["kind"] == "schedule" else [next(item for item in fixtures if _task_id(item) == task["id"])]
        compose_meta = compose_poster(
            background_path, poster_path, foreground_path, kind=task["kind"],
            fixtures=task_fixtures,
            predictions={_task_id(item): _prediction_block(item, run_dir) for item in task_fixtures},
            logo_path=ROOT / "assets/brand/jaguartv-logo.png",
            channels_root=ROOT / "assets/channels",
        )
        items.append({
            "task_id": task["id"],
            "kind": task["kind"],
            "prompt": str(prompt_path),
            "raw_background": str(raw_path),
            "background": str(background_path),
            "foreground": str(foreground_path),
            "poster": str(poster_path),
            "compose": compose_meta,
            "image_route": route,
        })
    manifest = {"ok": True, "status": "PHASE3_COMPLETE", "poster_count": len(items), "items": items}
    _write_json(phase_dir / "poster-manifest.json", manifest)
    return manifest


def run_phase4(config: FactoryConfig, run_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    posters = _read_json(run_dir / "phase3" / "poster-manifest.json").get("items", [])
    phase_dir = run_dir / "phase4"
    if not posters:
        report = {"ok": True, "status": "NO_CONTENT", "video_count": 0}
        _write_json(phase_dir / "build-manifest.json", report)
        return report

    inventory = discover_inventory(ROOT / "assets")
    inventory_id = inventory_fingerprint(inventory)
    rotation_state = ROOT / "runtime" / "media-rotation.json"
    usage_ids = {
        item["task_id"]: f"prematch:{run_dir.name}:{item['task_id']}:{inventory_id[:16]}"
        for item in posters
    }
    discard_stale_pending(rotation_state, set(usage_ids.values()))
    motion_plan = deterministic_motion_plan([item["task_id"] for item in posters], _run_date(run_dir))
    items = []
    for sequence, item in enumerate(posters, 1):
        usage_id = usage_ids[item["task_id"]]
        reservation = reserve_rotation(
            rotation_state, inventory, usage_id, operation_count=2, fingerprint=inventory_id,
        )
        selected = reservation["components"]
        components = {
            "operation": selected["operation"][0],
            "interface": selected["operation"][1],
            "cta": selected["cta"],
            "music": selected["music"],
            "voice": selected["voice"],
        }
        poster = Path(item["poster"])
        names = video_filenames(poster, int(config.data["video"].get("generation_seconds", 4)), sequence)
        out = phase_dir / names["media_stem"]
        master = out / names["master"]
        background_master = out / f"background-{names['master']}"
        foreground_master = out / f"foreground-{names['master']}"
        hook = out / names["hook"]
        final = out / names["final"]
        cover = out / names["cover"]
        master_info = make_layered_master(
            Path(item["background"]), Path(item["foreground"]), master,
            background_master, foreground_master,
        )
        Image.open(master).save(cover, "JPEG", quality=92)
        motion_prompt = _motion_prompt(item, background_master)
        motion_path = out / "motion-prompt.txt"
        motion_path.parent.mkdir(parents=True, exist_ok=True)
        motion_path.write_text(motion_prompt, encoding="utf-8")
        selected_for_motion = motion_plan[item["task_id"]]["dynamic"]
        if not selected_for_motion:
            raw = None
            _loop_video(master, hook, 4)
            generation = {"provider_id": "static-local", "model_id": "none", "task_id": None, "raw_video": None, "status": "not_called"}
        elif dry_run:
            raw = out / names["raw_video"]
            _loop_video(background_master, raw, int(config.data["video"].get("generation_seconds", 4)))
            generation = {"provider_id": "dry-run", "model_id": "none", "task_id": None, "raw_video": str(raw), "status": "not_called"}
        else:
            try:
                task_id = submit_dreamina_hook(background_master, motion_prompt, config.data["video"])
                raw = download_dreamina_result(task_id, out, config.data["video"].get("dreamina_command", "dreamina"))
                generation = {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"], "task_id": task_id, "raw_video": str(raw), "status": "ok"}
            except VideoGenerationError as primary_error:
                fallback = config.data["video"].get("fallback")
                if not fallback:
                    raise
                raw = out / f"apimart-{names['media_stem']}-4s.mp4"
                generation = generate_apimart_hook(background_master, motion_prompt, fallback, raw)
                generation["fallback_from"] = config.data["video"]["provider_id"]
                generation["attempts"] = [
                    {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"], "status": "failed", "sanitized_error": str(primary_error)[:500]},
                    {"provider_id": generation["provider_id"], "model_id": generation["model_id"], "status": "ok"},
                ]
        if selected_for_motion:
            assert raw is not None
            make_exact_hook(master, Path(raw), foreground_master, hook, 4.0)
        layer_qa = validate_layered_hook(hook, master, foreground_master)
        if not layer_qa["foreground_stable"]:
            raise RuntimeError(f"locked foreground changed during hook for {item['task_id']}: {layer_qa}")
        final_seconds = compose_v7(ROOT, master=master, hook=hook, output=final, components=components)
        _check_duration(final, final_seconds)
        av_duration = validate_av_duration(final, final_seconds)
        completed_item = {
            "task_id": item["task_id"],
            "generation_scope": "only poster image and 4-second poster hook are generated; all later segments are reused local assets",
            "sequence": sequence,
            "generated_seconds": 4,
            "final_seconds": round(final_seconds, 3),
            "audio_duration_qa": {**av_duration, "matches_video": abs(av_duration["audio"] - av_duration["video"]) <= 0.12},
            "middle_segment_policy": "operation-class videos play in full before CTA",
            "audio_policy": "local authorized music and CTA voice inventory only",
            "poster": str(poster),
            "master": str(master),
            "hook": str(hook),
            "final": str(final),
            "cover": str(cover),
            "cover_source": "full poster master",
            "motion_prompt": str(motion_path),
            "master_info": master_info,
            "background_master": str(background_master),
            "foreground_master": str(foreground_master),
            "motion_selection": motion_plan[item["task_id"]],
            "layer_qa": layer_qa,
            "components": components,
            "media_inventory_fingerprint": inventory_id,
            "asset_rotation": reservation,
            "video_generation": generation,
        }
        items.append(completed_item)
        _write_json(phase_dir / "build-manifest.json", {"status": "PHASE4_IN_PROGRESS", "items": items})
        commit_rotation(rotation_state, usage_id)
        completed_item["asset_rotation"]["committed"] = True
        _write_json(phase_dir / "build-manifest.json", {"status": "PHASE4_IN_PROGRESS", "items": items})
    captions = _captions(run_dir, items)
    _write_json(phase_dir / "captions.json", captions)
    manifest = {
        "ok": True,
        "status": "PHASE4_COMPLETE",
        "video_count": len(items),
        "generation_policy": {
            "generated": "poster plus 4-second dynamic hook only",
            "reused": "full operation-class videos and full motion CTA plus music and voice assets from repository inventory",
            "final_seconds": "dynamic: 4-second hook + full selected operation clips + full selected CTA",
            "video_filename_rule": "prefix every video base name with 01, 02, 03... in manifest order",
        },
        "items": items,
        "captions": str(phase_dir / "captions.json"),
    }
    _write_json(phase_dir / "build-manifest.json", manifest)
    return manifest


def run_phase5(config: FactoryConfig, run_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    videos = _read_json(run_dir / "phase4" / "build-manifest.json").get("items", [])
    phase_dir = run_dir / "phase5"
    if dry_run:
        result = {"ok": True, "status": "DRY_RUN_NOT_UPLOADED", "upload_count": 0, "would_upload": [item["final"] for item in videos]}
        _write_json(phase_dir / "upload-manifest.json", result)
        return result
    publishing = config.data["publishing"]
    uploads = []
    for item in videos:
        metadata = _upload_metadata(run_dir, item)
        upload = upload_pending_review(
            Path(item["final"]),
            metadata,
            base_url=config.env_value(publishing, "dashboard_url_env"),
            upload_token=config.env_value(publishing, "upload_token_env"),
            dashboard_token=config.env_value(publishing, "dashboard_token_env"),
        )
        uploads.append({"task_id": item["task_id"], **upload})
    result = {"ok": True, "status": "PHASE5_COMPLETE", "upload_count": len(uploads), "uploads": uploads}
    _write_json(phase_dir / "upload-manifest.json", result)
    return result


def run_all(config: FactoryConfig, run_dir: Path, *, dry_run: bool = False, fixtures_file: Path | None = None, research_dir: Path | None = None, target_date: str | None = None) -> dict[str, Any]:
    return {
        "phase1": run_phase1(config, run_dir, fixtures_file, target_date),
        "phase2": run_phase2(run_dir, dry_run=dry_run, research_dir=research_dir),
        "phase3": run_phase3(config, run_dir, dry_run=dry_run),
        "phase4": run_phase4(config, run_dir, dry_run=dry_run),
        "phase5": run_phase5(config, run_dir, dry_run=dry_run),
    }


def default_run_dir(target_date: str | None = None) -> Path:
    return ROOT / "runs" / (target_date or tomorrow_brasilia()).replace("-", "")


def _prediction_block(fixture: dict[str, Any], run_dir: Path) -> dict[str, str]:
    research_path = run_dir / "phase2" / f"{_task_id(fixture)}_research.json"
    if not research_path.is_file():
        return {"score": "", "probabilities": "", "tactical": ""}
    try:
        evidence = _read_json(research_path)
    except Exception:
        return {"score": "", "probabilities": "", "tactical": ""}
    entries = evidence.get("projected_or_inferred", []) if isinstance(evidence, dict) else []
    score = ""
    probabilities = ""
    tactical = ""
    for entry in entries:
        claim = str(entry.get("claim", ""))
        text = _clean_claim(entry)
        if not score and "prediction" in claim.lower() and re.search(r"\d\s*x\s*\d", text, re.IGNORECASE):
            score = text
        if not probabilities and "win/draw/loss" in claim.lower():
            probabilities = text
        if not tactical and "tactical" in claim.lower():
            tactical = text
    return {"score": score, "probabilities": probabilities, "tactical": tactical}


def _single_prompt_task(fixture: dict[str, Any], run_dir: Path) -> dict[str, str]:
    home, away = str(fixture["home_team"]), str(fixture["away_team"])
    match_date = str(fixture["schedule_date"])
    filename = f"{_zh(home)}_vs_{_zh(away)}_{_yyMMdd(match_date)}_海报.png"
    prompt = (
        "Create a clean cinematic 4:5 pre-match football background only. "
        f"Visual context: {home} versus {away}, {fixture['competition']}. "
        f"Home kit colours: {_colors(home)}. Away kit colours: {_colors(away)}. "
        "Leave the top 30%, center information band, and bottom 38% visually quiet. Absolutely no readable text, "
        "digits, typography, UI, panels, logos, crests, channel marks, sponsors, watermarks, or JaguarTV imagery."
    )
    return {"id": _task_id(fixture), "kind": "single", "filename": filename, "prompt": prompt, "title": f"{home} vs {away}"}


def _schedule_prompt_tasks(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dated = sorted(fixtures, key=lambda item: str(item.get("kickoff_at_brt", "")))
    match_date = str(dated[0]["schedule_date"])
    tasks = []
    for offset in range(0, len(dated), 8):
        page = offset // 8 + 1
        suffix = f"-{page:02d}" if len(dated) > 8 else ""
        tasks.append({
            "id": f"schedule-{_yyMMdd(match_date)}{suffix}", "kind": "schedule",
            "filename": f"{_yyMMdd(match_date)}_赛程海报{suffix}.png",
            "prompt": (
                "Create a premium 4:5 football schedule background only, with stadium depth and quiet space for a header "
                "and up to eight factual rows. Absolutely no readable text, digits, typography, UI, panels, logos, crests, "
                "channel marks, sponsors, watermarks, or JaguarTV imagery."
            ),
            "title": "Agenda JaguarTV", "fixtures": dated[offset : offset + 8],
        })
    return tasks


def _make_dry_poster(task: dict[str, str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1024, 1280))
    pixels = image.load()
    for y in range(image.height):
        tone = round(24 + 50 * y / image.height)
        for x in range(image.width):
            pixels[x, y] = (8, tone, 38)
    image.save(output)


def _dry_research(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "jaguartv-prematch-research-v1",
        "dry_run": True,
        "fixture_id": fixture["fixture_id"],
        "retrieval_time": _now(),
        "source_records": [{
            "source_url": fixture.get("source_url", ""),
            "platform": "fixture-file",
            "publication_time": fixture.get("retrieved_at", ""),
            "retrieval_time": _now(),
            "source_excerpt": fixture.get("source_text", ""),
            "normalized_summary": "Dry-run bridge evidence from the fixture record only.",
            "confidence": "dry-run",
        }],
    }


def _motion_prompt(item: dict[str, Any], master: Path) -> str:
    return (
        f"Animate only this text-free 9:16 football background for exactly 4 seconds: {master.name}. "
        "Use stadium lights, crowd depth, restrained sparks and cloth movement. Do not add text, scores, "
        "team graphics, channel icons, logos, watermarks, UI, or new people. The factual foreground is "
        "locked and composited locally after generation."
    )


def _loop_video(image: Path, output: Path, seconds: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(image), "-t", str(seconds), "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)], check=True, timeout=120)


def _check_duration(path: Path, expected: float) -> None:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True, timeout=30)
    actual = float(result.stdout.strip())
    if abs(actual - expected) > 0.25:
        raise RuntimeError(f"{path.name} duration {actual:.2f}s did not match {expected:.2f}s")


WEEKDAY_PT = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
DOWNLOAD_SENTENCE = "Acesse jaguartvbrasil.com/baixar-app para baixar."
TIKTOK_CAPTION_LIMIT = 360
TIKTOK_TITLE_LIMIT = 100
REQUIRED_HASHTAGS = ("#jaguartv", "#iptv")


def _captions(run_dir: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    date_iso = _run_date_iso(run_dir)
    return {
        "schema_version": "jaguartv-prematch-captions-v1",
        "language": "pt-BR",
        "timezone_label": "Horário de Brasília",
        "tiktok_policy": {
            "commercial_content_disclosure_required": True,
            "ai_generated_content_label_required": True,
            "max_hashtags": 5,
        },
        "items": [_caption_for(run_dir, item, fixtures, date_iso) for item in items],
        "fixture_count": len(fixtures),
    }


def _caption_for(run_dir: Path, item: dict[str, Any], fixtures: list[dict[str, Any]], date_iso: str) -> dict[str, Any]:
    weekday = WEEKDAY_PT[datetime.fromisoformat(date_iso).weekday()]
    if item.get("kind") == "schedule" or not _fixture_for(run_dir, item["task_id"]):
        hashtags = ["#futebol", "#brasileirao", "#palpites", "#jaguartv", "#iptv"]
        body = (
            f"🗓️ Agenda de {weekday}: {len(fixtures)} jogos para ficar de olho. "
            "Confira os confrontos e horários no vídeo. Jaguar TV no Android e TV Box."
        )
        return {
            "task_id": item["task_id"],
            "title": _short_text(f"Jogos de {weekday}: horários e onde assistir", TIKTOK_TITLE_LIMIT),
            "description": _tiktok_caption(body, hashtags),
            "hashtags": hashtags,
        }

    fx = _fixture_for(run_dir, item["task_id"]) or {}
    home, away = str(fx.get("home_team")), str(fx.get("away_team"))
    competition = str(fx.get("competition") or "")
    score = _predicted_score(run_dir, item["task_id"])
    tactical = _tactical_point(run_dir, item["task_id"])
    hashtags = _hashtags(home, away, competition)
    score_only = _score_only(score)
    prediction = f"{home} {score_only} {away}" if score_only else score
    tactical_copy = _short_text(tactical, 90)
    body = (
        f"🔥 Jogaço hoje: {home} x {away}, às {fx.get('kickoff_at_brt')}, pela {competition}. "
        f"Meu palpite: {prediction}."
        f"{(' ' + tactical_copy.rstrip('.') + '.') if tactical_copy else ''} "
        "Quer acompanhar? Jaguar TV no Android e TV Box."
    )
    return {
        "task_id": item["task_id"],
        "title": _prematch_title(home, away, score_only),
        "description": _tiktok_caption(body, hashtags),
        "hashtags": hashtags,
    }


def _hashtags(home: str, away: str, competition: str) -> list[str]:
    candidates = [
        _compact_hashtag(home),
        _compact_hashtag(away),
        _competition_hashtag(competition),
        "#futebol",
        "#palpites",
    ]
    selected: list[str] = []
    for tag in candidates:
        if tag not in selected and tag not in REQUIRED_HASHTAGS:
            selected.append(tag)
        if len(selected) == 3:
            break
    return selected + list(REQUIRED_HASHTAGS)


def _tag(value: str) -> str:
    value = value.lower()
    replacements = str.maketrans({"á": "a", "à": "a", "ã": "a", "â": "a", "é": "e", "ê": "e", "í": "i", "ó": "o", "ô": "o", "õ": "o", "ú": "u", "ü": "u", "ç": "c"})
    return re.sub(r"[^a-z0-9]+", "", value.translate(replacements)) or "futebol"


def _compact_hashtag(value: str) -> str:
    translated = value.lower().translate(str.maketrans({"á": "a", "à": "a", "ã": "a", "â": "a", "é": "e", "ê": "e", "í": "i", "ó": "o", "ô": "o", "õ": "o", "ú": "u", "ü": "u", "ç": "c"}))
    ignored = {"associacao", "club", "clube", "da", "das", "de", "do", "dos", "ec", "esporte", "fc", "football", "futebol", "saf", "sc"}
    words = [word for word in re.findall(r"[a-z0-9]+", translated) if word not in ignored]
    joined = "".join(words)
    if joined and len(joined) <= 20:
        return f"#{joined}"
    short = next((word for word in words if len(word) <= 20), "futebol")
    return f"#{short}"


def _competition_hashtag(value: str) -> str:
    slug = _tag(value)
    aliases = (
        (("championsleague", "ligadoscampeoes"), "#championsleague"),
        (("libertadores",), "#libertadores"),
        (("sudamericana", "sulamericana"), "#sulamericana"),
        (("brasileirao", "campeonatobrasileiro"), "#brasileirao"),
        (("copadobrasil",), "#copadobrasil"),
        (("premierleague",), "#premierleague"),
    )
    for names, hashtag in aliases:
        if any(name in slug for name in names):
            return hashtag
    return _compact_hashtag(value)


def _score_only(score: str) -> str:
    found = re.search(r"\b(\d+)\s*[xX]\s*(\d+)\b", score)
    return f"{found.group(1)} x {found.group(2)}" if found else ""


def _short_text(value: str, limit: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    shortened = value[: limit - 3].rsplit(" ", 1)[0].rstrip(".,:;!?")
    return f"{shortened or value[:limit - 3]}..."


def _prematch_title(home: str, away: str, score: str) -> str:
    prediction = f": palpite {score}" if score else ": palpite e onde assistir"
    suffix = " e onde assistir" if score else ""
    return _short_text(f"{home} x {away}{prediction}{suffix}", TIKTOK_TITLE_LIMIT)


def _tiktok_caption(body: str, hashtags: list[str]) -> str:
    suffix = f" {DOWNLOAD_SENTENCE} {' '.join(hashtags)}"
    return f"{_short_text(body, TIKTOK_CAPTION_LIMIT - len(suffix))}{suffix}"


def _predicted_score(run_dir: Path, task_id: str) -> str:
    """Render the projected score line as "Home 1 x 1 Away".

    Only the home side and the two digits used to survive, so every published caption read
    "Palpite JaguarTV: Coritiba 1 x 1." -- a truncated scoreline that never named the away
    team. The trailing team name is captured too, with a fallback for claims that stop at the
    digits.
    """
    claim = _projected_claim(run_dir, task_id, 0)
    found = re.search(
        r"([A-Za-zÀ-ÿ][\wÀ-ÿ .'-]*?)\s+(\d)\s*[xX]\s*(\d)\s*([A-Za-zÀ-ÿ][\wÀ-ÿ .'-]*)?",
        claim,
    )
    if not found:
        return f"{claim}" if claim else "jogo aberto, decisão nos detalhes"
    line = f"{found.group(1).strip()} {found.group(2)} x {found.group(3)}"
    away = (found.group(4) or "").strip()
    return f"{line} {away}".strip()


def _tactical_point(run_dir: Path, task_id: str) -> str:
    for entry in _projected_entries(run_dir, task_id):
        if "tactical" in str(entry.get("claim", "")).lower():
            return _clean_claim(entry)
    return ""


def _projected_claim(run_dir: Path, task_id: str, index: int) -> str:
    entries = _projected_entries(run_dir, task_id)
    return _clean_claim(entries[index]) if index < len(entries) else ""


def _projected_entries(run_dir: Path, task_id: str) -> list[dict[str, Any]]:
    path = run_dir / "phase2" / f"{task_id}_research.json"
    if not path.is_file():
        return []
    try:
        evidence = _read_json(path)
    except Exception:
        return []
    entries = evidence.get("projected_or_inferred", []) if isinstance(evidence, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _clean_claim(entry: dict[str, Any]) -> str:
    portuguese = str(entry.get("claim_pt", "")).strip()
    if portuguese:
        return portuguese
    claim = str(entry.get("claim", "")).strip()
    return re.sub(r"^Editorial\s+(prediction|tactical point|win/draw/loss read)\s*:\s*", "", claim, flags=re.IGNORECASE).strip()


def _upload_metadata(run_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    fx = _fixture_for(run_dir, item.get("task_id", ""))
    home = str(fx.get("home_team") or "").strip() if fx else ""
    away = str(fx.get("away_team") or "").strip() if fx else ""
    if fx:
        match_name = f"{home} vs {away}" if (home and away) else (item.get("task_id") or "unknown match")
    else:
        match_name = f"Agenda JaguarTV {_date_pt(_run_date_iso(run_dir))}"
    return {
        "category": "pre_match_prediction",
        "match_name": match_name,
        "match_date": (fx.get("schedule_date") if fx else None) or _run_date_iso(run_dir),
        "match_time_sao_paulo": _match_time_iso((fx.get("schedule_date") if fx else None) or _run_date_iso(run_dir), (fx.get("kickoff_at_brt") if fx else None) or "00:00"),
        "competition": (fx.get("competition") or "") if fx else "",
        "home_team": home,
        "away_team": away,
        "channels": (fx.get("channels") or ["Jaguar TV"]) if fx else ["Jaguar TV"],
        "kickoff_at_brt": (fx.get("kickoff_at_brt") or "") if fx else "",
        "match_info": {"content_category": "赛前预测", "task_id": item["task_id"], "timezone_label": "Horário de Brasília"},
        "metadata": {"artifact_revision": _sha256(Path(item["final"])), "run_dir": str(run_dir)},
    }


def _match_time_iso(date: str, kickoff: str) -> str:
    date = str(date or "").strip()
    time = str(kickoff or "").strip()
    if not date or not time:
        return ""
    if len(time) == 5:  # HH:MM -> HH:MM:SS
        time = f"{time}:00"
    return f"{date}T{time}-03:00"


def _run_date_iso(run_dir: Path) -> str:
    name = run_dir.name
    match = re.fullmatch(r"(\d{8})(?:_batch\d+)?", name)
    if match:
        value = match.group(1)
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return tomorrow_brasilia()


def _fixture_for(run_dir: Path, task_id: str) -> dict[str, Any] | None:
    """Locate the selected fixture matching a build-manifest task_id."""
    candidates = (run_dir / "phase1" / "selected-fixtures.json", run_dir / "phase1" / "fixtures.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = _read_json(path)
        except Exception:
            continue
        fixtures = list(data.get("fixtures", []))
        payload_fixtures = data.get("source_payload", {}).get("fixtures", []) if isinstance(data.get("source_payload"), dict) else []
        fixtures.extend(payload_fixtures)
        for fx in fixtures:
            if fx.get("fixture_id") == task_id or _task_id(fx) == task_id:
                return fx
    return None


def _colors(team: str) -> str:
    return TEAM_COLORS.get(team, TEAM_COLORS.get(_zh(team), f"the authentic {team} kit colours"))


def _zh(name: str) -> str:
    return TEAM_ZH.get(name, name)


def _yyMMdd(value: str) -> str:
    parsed = datetime.fromisoformat(value).date()
    return parsed.strftime("%y%m%d")


def _date_long_pt(value: str) -> str:
    parsed = datetime.fromisoformat(value).date()
    return f"{parsed.day:02d} de {MONTHS_PT_FULL[parsed.month - 1]} de {parsed.year}"


def _date_pt(value: str) -> str:
    parsed = datetime.fromisoformat(value).date()
    return f"{parsed.day:02d} {MONTHS_PT[parsed.month - 1]} {parsed.year}"


def _task_id(fixture: dict[str, Any]) -> str:
    raw = str(fixture.get("fixture_id") or f"{fixture.get('home_team')}-{fixture.get('away_team')}-{fixture.get('schedule_date')}")
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-") or hashlib.sha256(raw.encode()).hexdigest()[:12]


def _run_date(run_dir: Path) -> str:
    return run_dir.name if re.fullmatch(r"\d{8}", run_dir.name) else tomorrow_brasilia().replace("-", "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
