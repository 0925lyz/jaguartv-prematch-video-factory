from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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
    "(Prime Video, SporTV, Premiere, Globo, etc.) are added programmatically after generation. "
    "Do NOT invent text labels such as 'CANAL 554', 'JAGUARTV channel', 'JaguarTV channel', "
    "any TV-network wordmark or any channel name — those overlays are added programmatically. "
    "The only figurative animal allowed anywhere is the JaguarTV brand logo, and it is overlaid "
    "programmatically in the upper-right corner; the background must not redraw or echo it."
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

    tasks = [_single_prompt_task(item, run_dir) for item in fixtures]
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
        logo_overlay = _apply_fixed_logo(poster_path)
        channel_overlay: dict[str, Any] | None = None
        if task["kind"] == "schedule":
            channel_overlay = _apply_schedule_channel_logos(poster_path, fixtures, ROOT / "assets/channels")
        items.append({
            "task_id": task["id"],
            "kind": task["kind"],
            "prompt": str(prompt_path),
            "poster": str(poster_path),
            "fixed_logo_overlay": logo_overlay,
            "channel_logo_overlay": channel_overlay,
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
            "middle_segment_policy": "3-9s is assembled from operation-class videos only",
            "audio_policy": "APIMart-generated pt-BR CTA voice inventory only",
            "poster": str(poster),
            "master": str(master),
            "hook": str(hook),
            "final": str(final),
            "cover": str(cover),
            "cover_source": "full poster master",
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
            "reused": "3-9s operation-class videos, CTA, music, and APIMart voice assets from repository inventory",
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
    prediction = _prediction_block(fixture, run_dir)
    score_line = f"Predicted score (use exactly, do not invent): {prediction['score']}." if prediction["score"] else "Predicted score: use the team names and a small integer 0-3 on each side, matching the editorial read below."
    prob_line = f"Probability line (use exactly): {prediction['probabilities']}." if prediction["probabilities"] else "Probability line: Grêmio or Náutico first (depending on the home team), draw middle, away side last, all summing near 100, phrased as Probabilidade."
    tactical_line = f"Tactical note in one short Portuguese sentence (use exactly): {prediction['tactical']}" if prediction["tactical"] else "Tactical note: one short Portuguese sentence, factual, no betting language."
    prompt = (
        "Create a clean 4:5 JaguarTV pre-match prediction poster. All visible copy must be natural Brazilian Portuguese. "
        "Place the JaguarTV Figure 1 logo fixed in the upper-right corner. Keep all faces, crests, date, kickoff time, "
        f"and broadcast icons unobstructed. Match: {home} vs {away}. Competition: {fixture['competition']}. "
        f"Date: {_date_pt(match_date)}. Kickoff: {fixture['kickoff_at_brt']} Horário de Brasília. "
        f"Broadcast channels: {', '.join(fixture.get('channels') or [])}. "
        f"Home kit colours: {_colors(home)}. Away kit colours: {_colors(away)}. "
        f"Use one lower prediction box only, labeled PALPITE, with these three values in order: {score_line} {prob_line} {tactical_line} "
        "Text hierarchy inside that box must be predicted score first, then probability, then the tactical note, and it must stay readable at a 512px-wide thumbnail. "
        f"{CREST_SAFETY} {ANTI_FIGURE}"
    )
    return {"id": _task_id(fixture), "kind": "single", "filename": filename, "prompt": prompt, "title": f"{home} vs {away}"}


def _schedule_prompt_task(fixtures: list[dict[str, Any]]) -> dict[str, str]:
    dated = sorted(fixtures, key=lambda item: str(item.get("kickoff_at_brt", "")))
    match_date = str(dated[0]["schedule_date"])
    rows = "; ".join(f"{item['kickoff_at_brt']} {item['home_team']} vs {item['away_team']} PALPITE" for item in dated[:8])
    prompt = (
        "Create a 4:5 JaguarTV schedule poster in Brazilian Portuguese. Put the local match date at the upper center, "
        "use precise aligned rows. Each row layout MUST be exactly: time + empty channel-icon strip on the LEFT, "
        "home crest vs away crest with home strictly on the LEFT and away strictly on the RIGHT in the centre, "
        "and a clean EMPTY zone on the right edge reserved for the channel logo overlay. "
        "Do NOT draw any PALPITE wordmark tag, do NOT draw any channel name wordmark, do NOT draw any TV-network logo "
        "or text label inside the artwork — the channel logos and the PALPITE tag are added programmatically afterwards. "
        f"Date: {_date_pt(match_date)} Horário de Brasília. Rows: {rows}. "
        f"Give every row the correct club colours. {CREST_SAFETY} {ANTI_FIGURE}"
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


def _apply_fixed_logo(poster: Path) -> dict[str, Any]:
    logo_path = ROOT / "assets/brand/jaguartv-logo.png"
    with Image.open(poster) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    logo = ImageOps.exif_transpose(Image.open(logo_path)).convert("RGBA")
    width = max(96, round(image.width * 0.12))
    height = round(width * logo.height / logo.width)
    logo = logo.resize((width, height), Image.Resampling.LANCZOS)
    margin = max(20, round(image.width * 0.025))
    box = [image.width - width - margin, margin, image.width - margin, margin + height]
    clear_box = [
        max(0, image.width - round(width * 2.70) - margin),
        0,
        image.width,
        min(image.height, box[3] + round(height * 0.85)),
    ]
    _natural_fill_box(image, clear_box)
    image.alpha_composite(logo, (box[0], box[1]))
    image.convert("RGB").save(poster)
    return {
        "source": str(logo_path),
        "box": box,
        "cleared_box": clear_box,
        "safe_margin_px": margin,
        "background_fill": "natural_inpaint",
        "logo_sha256": _sha256(logo_path),
        "poster_sha256": _sha256(poster),
    }


def _natural_fill_box(image: Image.Image, box: list[int]) -> None:
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    region = image.crop((x0, y0, x1, y1)).convert("RGB")
    mask = Image.new("L", (width, height), 0)
    mask.putdata([
        255 if value > 178 or (saturation > 72 and value > 58) else 0
        for _, saturation, value in region.convert("HSV").getdata()
    ])
    mask = mask.filter(ImageFilter.MaxFilter(17)).filter(ImageFilter.GaussianBlur(3))
    if not mask.getbbox():
        return
    patch = image.crop((x0, y0, x1, y1)).convert("RGBA")
    pixels = patch.load()
    masked = {(idx % width, idx // width) for idx, value in enumerate(mask.getdata()) if value > 36}
    while masked:
        fills = []
        for x, y in masked:
            neighbours = [
                pixels[nx, ny]
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in masked
            ]
            if neighbours:
                fills.append((x, y, tuple(sum(pixel[i] for pixel in neighbours) // len(neighbours) for i in range(4))))
        if not fills:
            break
        for x, y, colour in fills:
            pixels[x, y] = colour
            masked.remove((x, y))
    image.paste(patch, (x0, y0))


# Channel-brand asset lookup: fixture channel names -> file stems in assets/channels/
_CHANNEL_ALIASES: dict[str, list[str]] = {
    "PRIME VIDEO": ["Prime_Video"],
    "PRIMEVIDEO": ["Prime_Video"],
    "AMAZON PRIME": ["Prime_Video"],
    "SPORTV / PREMIERE": ["SporTV", "Premiere"],
    "SPORTV/PREMIERE": ["SporTV", "Premiere"],
    "PREMIERE": ["Premiere"],
    "SPORTV": ["SporTV"],
    "TV GLOBO": ["TV_Globo"],
    "GLOBO": ["TV_Globo"],
    "GLOBO/SPORTV/PREMIERE/PRIME VIDEO": ["TV_Globo", "SporTV", "Premiere", "Prime_Video"],
    "XSPORTS/YOUTUBE": ["XSports", "YouTube"],
}


def _resolve_channel_assets(channel_label: str, channels_root: Path) -> list[Path]:
    label = channel_label.strip().upper()
    stems = _CHANNEL_ALIASES.get(label) or _CHANNEL_ALIASES.get(label.split(" / ")[0], [label.split(" / ")[0]])
    assets: list[Path] = []
    for stem in stems:
        for ext in (".png", ".jpg", ".jpeg", ".ico"):
            candidate = channels_root / f"{stem}{ext}"
            if candidate.is_file():
                assets.append(candidate)
                break
    return assets


def _apply_schedule_channel_logos(
    poster: Path,
    fixtures: list[dict[str, Any]],
    channels_root: Path,
) -> dict[str, Any]:
    """Programmatically overlay official channel-brand logos onto the schedule poster.

    For each fixture row the right-side band is first painted with the poster's dark dominant
    colour to overwrite any AI-generated PALPITE / channel wordmark, then the official channel
    logos are alpha-composited centred inside that band.
    """
    sorted_fixtures = sorted(fixtures, key=lambda item: str(item.get("kickoff_at_brt", "")))
    with Image.open(poster) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    width, height = image.size
    band_left = round(width * 0.79)
    band_right = round(width * 0.99)
    band_top = round(height * 0.30)
    band_bottom = round(height * 0.92)
    band_height = band_bottom - band_top
    row_count = max(1, len(sorted_fixtures))
    row_h = band_height // row_count
    mask_color = (10, 18, 30, 235)
    mask_draw = ImageDraw.Draw(image)
    placements: list[dict[str, Any]] = []
    for idx, fixture in enumerate(sorted_fixtures):
        channels = fixture.get("channels") or ["Jaguar TV"]
        assets: list[Path] = []
        for label in channels:
            assets.extend(_resolve_channel_assets(label, channels_root))
        if not assets:
            continue
        row_y0 = band_top + row_h * idx
        row_y1 = row_y0 + row_h
        mask_draw.rectangle([band_left, row_y0, band_right, row_y1], fill=mask_color)
        target_w_each = round((band_right - band_left) * 0.40)
        target_w_each = min(target_w_each, 150)
        gap = round(target_w_each * 0.18)
        total_w = len(assets) * target_w_each + max(0, len(assets) - 1) * gap
        centre_x = (band_left + band_right) // 2
        start_x = centre_x - total_w // 2
        y_center = (row_y0 + row_y1) // 2
        for offset, asset_path in enumerate(assets):
            try:
                with Image.open(asset_path) as source_icon:
                    icon = ImageOps.exif_transpose(source_icon).convert("RGBA")
            except Exception:
                continue
            ratio = target_w_each / icon.width
            new_h = round(icon.height * ratio)
            icon = icon.resize((target_w_each, new_h), Image.Resampling.LANCZOS)
            x = start_x + offset * (target_w_each + gap)
            y = y_center - new_h // 2
            image.alpha_composite(icon, (x, y))
            placements.append({"task_id": fixture.get("fixture_id"), "channel": asset_path.stem,
                               "box": [x, y, x + target_w_each, y + new_h], "sha256": _sha256(asset_path)})
    image.convert("RGB").save(poster)
    return {"poster_sha256": _sha256(poster), "channel_logo_overlays": placements}


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
    operation = sorted(str(path) for path in (root / "assets/video/operation").glob("*.mp4") if "master" not in path.name.lower())
    if len(operation) < 2:
        operation = sorted(str(path) for path in (root / "assets/video/operation").glob("*.mp4"))
    cta = sorted(str(path) for path in (root / "assets/video/cta").glob("**/*") if path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png"})
    music = sorted(str(path) for path in (root / "assets/audio/music").glob("*") if path.suffix.lower() in {".mp3", ".m4a", ".wav"})
    voice = sorted(str(path) for path in (root / "assets/audio/voiceover").glob("*apimart*.wav"))
    return {"operation": operation, "interface": operation, "cta": cta, "music": music, "voice": voice}


def _motion_prompt(item: dict[str, Any], master: Path) -> str:
    return (
        f"Animate this exact 9:16 JaguarTV pre-match master for 4 seconds: {master.name}. "
        "Keep the poster-cover composition visible as the dominant full-frame subject throughout the first 3 seconds; do not treat it as a single-frame flash. "
        "Preserve all Brazilian Portuguese text, player identity, club kit, crests, channel icons, date, kickoff time, prediction, and the upper-right JaguarTV logo. "
        "Make the poster background visibly alive with stadium lights, crowd depth, sparks, cloth movement, and a fierce face-to-face player confrontation when two players are present. "
        "Keep every logo, face, text block, and score readable and fixed in identity; no face obstruction."
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


def _captions(run_dir: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    date_iso = _run_date_iso(run_dir)
    return {
        "schema_version": "jaguartv-prematch-captions-v1",
        "language": "pt-BR",
        "timezone_label": "Horário de Brasília",
        "items": [_caption_for(run_dir, item, fixtures, date_iso) for item in items],
        "fixture_count": len(fixtures),
    }


def _caption_for(run_dir: Path, item: dict[str, Any], fixtures: list[dict[str, Any]], date_iso: str) -> dict[str, Any]:
    weekday = WEEKDAY_PT[datetime.fromisoformat(date_iso).weekday()]
    date_pt = _date_long_pt(date_iso)
    if item.get("kind") == "schedule" or not _fixture_for(run_dir, item["task_id"]):
        rows = "\n".join(
            f"{fx['kickoff_at_brt']} — {fx['home_team']} x {fx['away_team']} ({fx['competition']}) · " + " / ".join(fx.get("channels") or ["Jaguar TV"])
            for fx in sorted(fixtures, key=lambda row: str(row.get("kickoff_at_brt", "")))
        )
        return {
            "task_id": item["task_id"],
            "title": f"Agenda de {weekday} na JaguarTV",
            "description": (
                f"Agenda de {weekday}, {date_pt} — horários de Brasília:\n{rows}\n"
                "Palpites no vídeo. Comenta aqui quem você acha que ganha!"
            ),
            "tags": ["JaguarTV", "AgendaDeJogos", "Futebol", "Palpites"],
        }

    fx = _fixture_for(run_dir, item["task_id"]) or {}
    home, away = str(fx.get("home_team")), str(fx.get("away_team"))
    channels = " / ".join(fx.get("channels") or ["Jaguar TV"])
    competition = str(fx.get("competition") or "")
    score = _predicted_score(run_dir, item["task_id"])
    tactical = _tactical_point(run_dir, item["task_id"])
    description = (
        f"{home} x {away} — {competition}. {weekday}, {date_pt}, às {fx.get('kickoff_at_brt')} "
        f"(Horário de Brasília), ao vivo na {channels}.\n"
        f"Palpite JaguarTV: {score}.\n{tactical}"
    )
    return {
        "task_id": item["task_id"],
        "title": f"Palpite JaguarTV: {home} x {away}",
        "description": description,
        "tags": ["JaguarTV", "Palpite", "Futebol"] + [name.replace(" ", "") for name in (home, away)],
    }


def _predicted_score(run_dir: Path, task_id: str) -> str:
    claim = _projected_claim(run_dir, task_id, 0)
    found = re.search(r"([A-Za-zÀ-ÿ][\wÀ-ÿ .'-]*?)\s+(\d)\s*[xX]\s*(\d)", claim)
    if not found:
        return f"{claim}" if claim else "jogo aberto, decisão nos detalhes"
    return f"{found.group(1).strip()} {found.group(2)} x {found.group(3)}"


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
    if re.fullmatch(r"\d{8}", name):
        return f"{name[0:4]}-{name[4:6]}-{name[6:8]}"
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
