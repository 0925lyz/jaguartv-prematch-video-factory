from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .collector import FixtureCollectionError, collect_fixtures, load_fixture_file, save_collection, tomorrow_brasilia
from .config import FactoryConfig
from .image2 import generate_image2, save_image_route_manifest
from .records import Fixture
from .routing import CodexDeepSeekRouter
from .selection import select_fixtures
from .upload import upload_pending_review
from .video import (
    compose_v7,
    deterministic_batch_rotation,
    download_dreamina_result,
    make_exact_hook,
    make_vertical_master,
    submit_dreamina_hook,
    video_filenames,
)


ROOT = Path(__file__).resolve().parents[2]
MONTHS_PT = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
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
    selected = {
        "date": target_date or tomorrow_brasilia(),
        "timezone_label": "Horário de Brasília",
        "source": source,
        "fixtures": select_fixtures(fixtures),
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

    tasks = [_single_prompt_task(item) for item in fixtures]
    tasks.append(_schedule_prompt_task(fixtures))
    items = []
    for task in tasks:
        prompt_path = phase_dir / "prompts" / f"{task['id']}.txt"
        poster_path = phase_dir / "posters" / task["filename"]
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(task["prompt"], encoding="utf-8")
        if dry_run:
            _make_dry_poster(task, poster_path)
            route = [{"provider_id": "dry-run", "model_id": "none", "status": "not_called"}]
        else:
            attempts = generate_image2(task["prompt"], poster_path, config.data["image"])
            save_image_route_manifest(phase_dir / "image-routes" / f"{task['id']}.json", attempts)
            route = [item.__dict__ for item in attempts]
        items.append({"task_id": task["id"], "kind": task["kind"], "prompt": str(prompt_path), "poster": str(poster_path), "image_route": route})
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

    pools = _component_pools(ROOT)
    rotations = deterministic_batch_rotation(_run_date(run_dir), [item["task_id"] for item in posters], pools)
    items = []
    for item in posters:
        poster = Path(item["poster"])
        names = video_filenames(poster, int(config.data["video"].get("generation_seconds", 4)))
        out = phase_dir / names["media_stem"]
        master = out / names["master"]
        hook = out / names["hook"]
        final = out / names["final"]
        cover = out / names["cover"]
        master_info = make_vertical_master(poster, master)
        Image.open(master).save(cover, "JPEG", quality=92)
        motion_prompt = _motion_prompt(item, master)
        motion_path = out / "motion-prompt.txt"
        motion_path.parent.mkdir(parents=True, exist_ok=True)
        motion_path.write_text(motion_prompt, encoding="utf-8")
        if dry_run:
            raw = out / names["raw_video"]
            _loop_video(master, raw, int(config.data["video"].get("generation_seconds", 4)))
            dreamina = {"provider_id": "dry-run", "task_id": None, "raw_video": str(raw)}
        else:
            task_id = submit_dreamina_hook(master, motion_prompt, config.data["video"])
            raw = download_dreamina_result(task_id, out, config.data["video"].get("dreamina_command", "dreamina"))
            dreamina = {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"], "task_id": task_id, "raw_video": str(raw)}
        make_exact_hook(master, raw, hook)
        compose_v7(ROOT, master=master, hook=hook, output=final, components=rotations[item["task_id"]])
        _check_duration(final, 12.0)
        items.append({
            "task_id": item["task_id"],
            "generation_scope": "only poster image and first 3-second poster hook are generated; all later segments are reused local assets",
            "generated_seconds": 3,
            "reused_seconds": 9,
            "poster": str(poster),
            "master": str(master),
            "hook": str(hook),
            "final": str(final),
            "cover": str(cover),
            "motion_prompt": str(motion_path),
            "master_info": master_info,
            "components": rotations[item["task_id"]],
            "dreamina": dreamina,
        })
    captions = _captions(run_dir, items)
    _write_json(phase_dir / "captions.json", captions)
    manifest = {
        "ok": True,
        "status": "PHASE4_COMPLETE",
        "video_count": len(items),
        "generation_policy": {
            "generated": "poster plus 3-second dynamic hook only",
            "reused": "operation, interface, CTA, music, and voice assets from repository inventory",
            "final_seconds": 12,
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


def _single_prompt_task(fixture: dict[str, Any]) -> dict[str, str]:
    home, away = str(fixture["home_team"]), str(fixture["away_team"])
    match_date = str(fixture["schedule_date"])
    filename = f"{_zh(home)}_vs_{_zh(away)}_{_yyMMdd(match_date)}_海报.png"
    prompt = (
        "Create a clean 4:5 JaguarTV pre-match prediction poster. All visible copy must be natural Brazilian Portuguese. "
        "Place the JaguarTV Figure 1 logo fixed in the upper-right corner. Keep all faces, crests, date, kickoff time, "
        f"and broadcast icons unobstructed. Match: {home} vs {away}. Competition: {fixture['competition']}. "
        f"Date: {_date_pt(match_date)}. Kickoff: {fixture['kickoff_at_brt']} Horário de Brasília. "
        f"Broadcast channels: {', '.join(fixture.get('channels') or [])}. "
        "Use one lower prediction box only, labeled PALPITE, with a short factual prediction and predicted score. "
        "Use current licensed player photos only when verified by Phase 2 evidence; do not invent transferred players."
    )
    return {"id": _task_id(fixture), "kind": "single", "filename": filename, "prompt": prompt, "title": f"{home} vs {away}"}


def _schedule_prompt_task(fixtures: list[dict[str, Any]]) -> dict[str, str]:
    dated = sorted(fixtures, key=lambda item: str(item.get("kickoff_at_brt", "")))
    match_date = str(dated[0]["schedule_date"])
    rows = "; ".join(f"{item['kickoff_at_brt']} {item['home_team']} vs {item['away_team']} PALPITE" for item in dated[:8])
    prompt = (
        "Create a 4:5 JaguarTV schedule poster in Brazilian Portuguese. Put the local match date at the upper center, "
        "use precise aligned rows, kickoff time and JaguarTV channel icons on the left, home crest/team vs away team/crest, "
        f"and prefix every prediction with PALPITE. Date: {_date_pt(match_date)} Horário de Brasília. Rows: {rows}."
    )
    return {"id": f"schedule-{_yyMMdd(match_date)}", "kind": "schedule", "filename": f"{_yyMMdd(match_date)}_赛程海报.png", "prompt": prompt, "title": "Agenda JaguarTV"}


def _make_dry_poster(task: dict[str, str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1024, 1280), (12, 92, 48))
    draw = ImageDraw.Draw(img)
    logo = ImageOps.exif_transpose(Image.open(ROOT / "assets/brand/jaguartv-logo.png")).convert("RGBA")
    logo.thumbnail((170, 170))
    img.paste(logo, (830, 28), logo)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 54)
    small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    draw.text((60, 260), "PRÉ-JOGO", fill=(255, 230, 70), font=font)
    draw.text((60, 350), task["title"][:34], fill=(255, 255, 255), font=font)
    draw.text((60, 960), "PALPITE: jogo equilibrado", fill=(255, 255, 255), font=small)
    draw.text((60, 1010), "Horário de Brasília", fill=(255, 230, 70), font=small)
    img.save(output)


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


def _component_pools(root: Path) -> dict[str, list[str]]:
    operation = sorted(str(path) for path in (root / "assets/video/operation").glob("*.mp4") if "epg" not in path.name.lower())
    interface = sorted(str(path) for path in (root / "assets/video/operation").glob("*.mp4") if "epg" in path.name.lower())
    cta = sorted(str(path) for path in (root / "assets/video/cta").glob("**/*") if path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png"})
    music = sorted(str(path) for path in (root / "assets/audio/music").glob("*") if path.suffix.lower() in {".mp3", ".m4a", ".wav"})
    voice = sorted(str(path) for path in (root / "assets/audio/voiceover").glob("*.wav"))
    return {"operation": operation, "interface": interface, "cta": cta, "music": music, "voice": voice}


def _motion_prompt(item: dict[str, Any], master: Path) -> str:
    return (
        f"Animate this exact 9:16 JaguarTV pre-match master for 4 seconds: {master.name}. "
        "Preserve all Brazilian Portuguese text, player identity, club kit, crests, channel icons, date, kickoff time, prediction, and the upper-right JaguarTV logo. "
        "Use restrained stadium lighting, subtle push-in, and no face obstruction."
    )


def _loop_video(image: Path, output: Path, seconds: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(image), "-t", str(seconds), "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)], check=True, timeout=120)


def _check_duration(path: Path, expected: float) -> None:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True, timeout=30)
    actual = float(result.stdout.strip())
    if abs(actual - expected) > 0.25:
        raise RuntimeError(f"{path.name} duration {actual:.2f}s did not match {expected:.2f}s")


def _captions(run_dir: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    return {
        "schema_version": "jaguartv-prematch-captions-v1",
        "timezone_label": "Horário de Brasília",
        "items": [{"task_id": item["task_id"], "title": "Palpite do dia na JaguarTV", "description": "Agenda e análise pré-jogo com horários de Brasília.", "tags": ["JaguarTV", "Palpite", "Futebol"]} for item in items],
        "fixture_count": len(selected.get("fixtures", [])),
    }


def _upload_metadata(run_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": "pre_match_prediction",
        "match_info": {"content_category": "赛前预测", "task_id": item["task_id"], "timezone_label": "Horário de Brasília"},
        "metadata": {"artifact_revision": _sha256(Path(item["final"])), "run_dir": str(run_dir)},
    }


def _zh(name: str) -> str:
    return TEAM_ZH.get(name, name)


def _yyMMdd(value: str) -> str:
    parsed = datetime.fromisoformat(value).date()
    return parsed.strftime("%y%m%d")


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
