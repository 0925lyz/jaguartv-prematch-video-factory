#!/usr/bin/env python3
"""JaguarTV Prematch Video Factory — canonical Task 1 automation driver.

This driver is the single WorkBuddy/Codex entrypoint for the 任务一 workflow.
It lives in the repository, reuses jaguartv_prematch.* modules for every media call,
and adds the daily execution details the stock `run_all` intentionally keeps small:

  * Batch-specific poster STYLE (prematch style pool + history in image2数据库).
  * Batch-specific video-component ROTATION (date_seed = f"{date}-B{batch}").
  * Batch-specific pt-BR CAPTIONS.

Run artifacts live under <repo>/runs/<date>_batch<N> (gitignored). Desktop
delivery folders remain outside the repo.

Run it from the automation (or manually):
    source <repo>/.venv/bin/activate
    python task1_driver.py --batch auto --dry-run      # validate wiring, no credits
    python task1_driver.py --batch auto                 # real production run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- make the repository importable ------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO / "src"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jaguartv_prematch.config import FactoryConfig  # noqa: E402
from jaguartv_prematch.image2 import (  # noqa: E402
    generate_image2,
    save_image_route_manifest,
)
from jaguartv_prematch.pipeline import (  # noqa: E402
    ANTI_FIGURE,
    CREST_SAFETY,
    ROOT as REPO_ROOT,
    WEEKDAY_PT,
    _apply_fixed_logo,
    _apply_schedule_channel_logos,
    _check_duration,
    _colors,
    _component_pools,
    _date_long_pt,
    _date_pt,
    _fixture_for,
    _hashtags,
    _loop_video,
    _predicted_score,
    _prediction_block,
    _run_date_iso,
    _tactical_point,
    _task_id,
    _upload_metadata,
    _yyMMdd,
    _zh,
)
from jaguartv_prematch.upload import upload_pending_review  # noqa: E402
from jaguartv_prematch.video import (  # noqa: E402
    compose_v7,
    deterministic_batch_rotation,
    download_dreamina_result,
    make_exact_hook,
    make_vertical_master,
    submit_dreamina_hook,
    video_filenames,
)

BRT = timezone(timedelta(hours=-3))
STYLE_POOL = Path("/Users/jaguar/Documents/ChatGPT/海报自动生成/image2数据库/docs/prematch-style-pool.md")
STYLE_HISTORY = Path("/Users/jaguar/Documents/ChatGPT/海报自动生成/image2数据库/style_history/prematch_styles.json")
DESKTOP = Path("/Users/jaguar/Desktop")
LARK_CLI = os.environ.get("JAGUARTV_LARK_CLI", "/Users/jaguar/.workbuddy/binaries/node/workspace/node_modules/.bin/lark-cli")
LARK_GROUP = os.environ.get("JAGUARTV_LARK_GROUP", "oc_1cde04275466df4d7369f5129485f2cf")


# --------------------------------------------------------------------------------------
def _now_brt() -> datetime:
    return datetime.now(BRT)


def _brt_hour() -> int:
    return _now_brt().hour


def _tomorrow_brt_yyyymmdd() -> str:
    return (_now_brt() + timedelta(days=1)).strftime("%Y%m%d")


def _detect_batch() -> int:
    return 1 if _brt_hour() < 14 else 2


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def _sync_repo() -> None:
    r = _run(["git", "-C", str(REPO), "pull", "--rebase", "origin", "main"], timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"repo sync failed: {r.stderr or r.stdout}"[:800])


def _names(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(i) for i in value if i]


# --- style rotation -------------------------------------------------------------------
def _load_history() -> dict:
    if STYLE_HISTORY.is_file():
        return _read_json(STYLE_HISTORY)
    return {"version": 1, "max_recent_styles": 3, "pool": [
        "broadcast_green_gold", "cinematic_night", "stadium_collage",
        "epic_symbolic", "retro_print_grunge", "neon_editorial"], "recent_styles": []}


def _save_history(hist: dict) -> None:
    _write_json(STYLE_HISTORY, hist)


def _select_style(hist: dict, batch: int, batch1_style: str | None) -> dict:
    pool = hist.get("pool", [])
    recent = hist.get("recent_styles", [])
    candidates = [s for s in pool if s not in recent]
    if batch == 2 and batch1_style:
        candidates = [s for s in candidates if s != batch1_style]
    if not candidates:
        candidates = [s for s in pool if s != (batch1_style or (recent[-1] if recent else None))]
    if not candidates:
        candidates = list(pool)
    seed = int(hashlib.sha256(f"{batch}:{','.join(pool)}".encode()).hexdigest(), 16)
    chosen = candidates[seed % len(candidates)]
    return {
        "eligible_styles": candidates,
        "excluded_recent_styles": recent,
        "selected_style": chosen,
        "selection_seed": seed,
    }


def _commit_style(hist: dict, style: str, max_recent: int = 3) -> None:
    recent = [s for s in hist.get("recent_styles", []) if s != style]
    recent.append(style)
    hist["recent_styles"] = recent[-max_recent:]
    _save_history(hist)


# --- players --------------------------------------------------------------------------
def _players_for(fixture: dict) -> tuple[list[str], list[str]]:
    home_stars = _names(fixture.get("home_verified_players") or fixture.get("home_player_assets"))
    away_stars = _names(fixture.get("away_verified_players") or fixture.get("away_player_assets"))
    # de-dup while keeping order
    home_stars = list(dict.fromkeys(home_stars))[:2]
    away_stars = list(dict.fromkeys(away_stars))[:2]
    return home_stars, away_stars


# --- poster prompt (real-player likeness + batch style) --------------------------------
def _single_poster_prompt(fixture: dict, style_scene: str, home_stars: list[str], away_stars: list[str],
                          prediction: dict[str, str] | None = None) -> str:
    """Full-copy single-match poster prompt (r2 gold-standard layout).

    Image2 renders EVERYTHING: PRÉ-JOGO header, competition, date/kickoff/channel info row,
    both crests with team names, and ONE bottom PALPITE box. Style only steers mood/grading —
    it must never suppress the copy. Real star players frame the edges without covering text.
    """
    home, away = str(fixture["home_team"]), str(fixture["away_team"])
    p_home = home_stars[0] if home_stars else "an anonymous hardman footballer representing the home team"
    p_away = away_stars[0] if away_stars else "an anonymous hardman footballer representing the away team"
    p_home_b = home_stars[1] if len(home_stars) > 1 else p_home
    p_away_b = away_stars[1] if len(away_stars) > 1 else p_away
    prediction = prediction or {}
    score = prediction.get("score") or f"{home} with a small integer 0-3 on each side"
    probabilities = prediction.get("probabilities") or (
        f"{home} first, Empate middle, {away} last, three percentages summing near 100%")
    tactical = prediction.get("tactical") or "one short factual Brazilian Portuguese tactical sentence"
    return (
        "Create a premium 4:5 JaguarTV pre-match football prediction poster, final composition "
        "2048x2560. ALL visible copy must be natural Brazilian Portuguese with correct accents, "
        "crisp, perfectly legible and rendered by you — this is a finished poster, not a background. "
        f"Visual mood: {style_scene} "
        "Layout, top to bottom: "
        "(1) Header, pinned to the LEFT side of the canvas: the exact word 'PRÉ-JOGO' as a bold "
        "LEFT-ALIGNED title that starts at the left margin and ends before the 55% width mark — it "
        "must never extend past the horizontal centre of the canvas, so keep the type small enough; "
        "the complete word 'PRÉ-JOGO' must be fully readable, no letter clipped by any edge. The "
        f"competition name '{fixture.get('competition')}' sits directly beneath it, also left-aligned "
        "and fully inside the frame. The entire right ~45% of the header zone stays empty (brand logo "
        "is baked in programmatically). "
        "(2) Info row with three small labelled cells: DATA "
        f"'{_date_pt(str(fixture['schedule_date']))}', HORÁRIO '{fixture.get('kickoff_at_brt')}' with "
        "'Horário de Brasília' under it, TRANSMISSÃO "
        f"'{', '.join(fixture.get('channels') or ['Jaguar TV'])}'. "
        f"(3) Middle band: the official {home} crest strictly on the LEFT and the official {away} crest "
        "strictly on the RIGHT, a large 'X' between them, and each team's name in bold capitals under "
        "its crest. "
        "(4) Bottom: exactly ONE dark prediction panel labeled 'PALPITE' containing, in this order: the "
        f"predicted score '{score}' in large type, the probability line '{probabilities}', and the "
        f"tactical note '{tactical}'. No second prediction box anywhere. "
        f"Players: photorealistic likeness of {p_home} and {p_home_b} in {home} current official kit "
        f"({_colors(home)}) framing the far LEFT edge, and {p_away} and {p_away_b} in {away} current "
        f"official kit ({_colors(away)}) framing the far RIGHT edge, waist-up, facing each other; they "
        "must never overlap or cover any text, crest, or the prediction panel, and nothing may sit on "
        "or above their heads, faces or shoulders. "
        "Keep the upper-right corner completely empty of text and graphics — the JaguarTV brand logo "
        "is baked in programmatically afterwards; do NOT render any 'JaguarTV' wordmark yourself. "
        "Negative constraints: no betting advice, no odds, no 18+, no gambling or responsible-gambling "
        "copy, no English body text, no invented or garbled words, no fake scores, no fake crests, no "
        "extra panels or banners beyond the layout above. "
        f"{CREST_SAFETY} {ANTI_FIGURE}"
    )


def _schedule_poster_prompt(fixtures: list[dict], style_scene: str) -> str:
    dated = sorted(fixtures, key=lambda i: str(i.get("kickoff_at_brt", "")))
    match_date = str(dated[0]["schedule_date"])
    rows = "; ".join(f"{i['kickoff_at_brt']} {i['home_team']} vs {i['away_team']}" for i in dated[:8])
    return (
        "Create a 4:5 JaguarTV 'AGENDA DE JOGOS' schedule poster in Brazilian Portuguese — a finished "
        "poster, not a background: all copy must be rendered by you, crisp and perfectly legible. "
        f"Visual mood: {style_scene} "
        "Header: a bold 'AGENDA DE JOGOS' title with the match date "
        f"'{_date_pt(match_date)}' and 'Horário de Brasília' beneath it. The header (title, date and "
        "all lettering) must stay within the LEFT and CENTRE of the canvas (the left 60% of the "
        "width); the entire top-right region — the right ~40% of the width across the top ~18% of "
        "the height — must remain completely empty: no letters, no shapes, no textures change, because "
        "the JaguarTV brand logo is baked in there programmatically. "
        "Body: one precisely aligned row per match, each row layout MUST be exactly: kickoff time on "
        "the LEFT, then the home club crest strictly LEFT vs the away club crest strictly RIGHT in the "
        "centre with 'VS' between them, both team names in bold capitals under their crests, and a "
        "clean EMPTY vertical band along the RIGHT edge (x 79%-99% of width) reserved for channel "
        "logos that are overlaid programmatically afterwards. EVERY word in every row (kickoff "
        "times and both team names) must fit entirely inside the canvas, ending well before the "
        "reserved right band — scale the type down if needed; no letter may touch or cross a canvas "
        "edge. "
        "Do NOT draw any PALPITE wordmark, channel name wordmark, or TV-network logo inside the "
        "artwork, and keep the upper-right corner free of text (brand logo is overlaid afterwards). "
        f"Rows: {rows}. "
        f"Give every row the correct club colours. {CREST_SAFETY} {ANTI_FIGURE}"
    )


# --- phase 3 ---------------------------------------------------------------------------
def _phase3(config, run_dir: Path, style: str, style_scene: str, dry_run: bool) -> dict:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    phase_dir = run_dir / "phase3"
    if not fixtures:
        report = {"ok": True, "status": "NO_CONTENT", "poster_count": 0}
        _write_json(phase_dir / "poster-manifest.json", report)
        return report

    tasks = []
    for fx in fixtures:
        home_stars, away_stars = _players_for(fx)
        tasks.append({
            "id": _task_id(fx), "kind": "single",
            "filename": f"{_zh(str(fx['home_team']))}_vs_{_zh(str(fx['away_team']))}_{_yyMMdd(str(fx['schedule_date']))}_海报.png",
            "prompt": _single_poster_prompt(fx, style_scene, home_stars, away_stars,
                                            prediction=_prediction_block(fx, run_dir)),
            "title": f"{fx['home_team']} vs {fx['away_team']}",
        })
    tasks.append({
        "id": f"schedule-{_yyMMdd(str(fixtures[0]['schedule_date']))}", "kind": "schedule",
        "filename": f"{_yyMMdd(str(fixtures[0]['schedule_date']))}_赛程海报.png",
        "prompt": _schedule_poster_prompt(fixtures, style_scene), "title": "Agenda JaguarTV",
    })

    items = []
    for task in tasks:
        prompt_path = phase_dir / "prompts" / f"{task['id']}.txt"
        poster_path = phase_dir / "posters" / task["filename"]
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(task["prompt"], encoding="utf-8")
        if dry_run:
            _make_dry_poster(poster_path)
            route = [{"provider_id": "dry-run", "model_id": "none", "status": "not_called"}]
        else:
            attempts = generate_image2(task["prompt"], poster_path, config.data["image"])
            save_image_route_manifest(phase_dir / "image-routes" / f"{task['id']}.json", attempts)
            route = [a.__dict__ for a in attempts]
        logo_overlay = _apply_fixed_logo(poster_path)
        channel_overlay = None
        if task["kind"] == "schedule":
            channel_overlay = _apply_schedule_channel_logos(poster_path, fixtures, REPO_ROOT / "assets" / "channels")
        items.append({
            "task_id": task["id"], "kind": task["kind"], "prompt": str(prompt_path),
            "poster": str(poster_path), "fixed_logo_overlay": logo_overlay,
            "channel_logo_overlay": channel_overlay, "image_route": route,
        })
    manifest = {"ok": True, "status": "PHASE3_COMPLETE", "poster_count": len(items), "items": items}
    _write_json(phase_dir / "poster-manifest.json", manifest)
    return manifest


def _make_dry_poster(output: Path) -> None:
    from PIL import Image, ImageDraw, ImageOps
    output.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1024, 1280), (12, 92, 48))
    draw = ImageDraw.Draw(img)
    logo = ImageOps.exif_transpose(Image.open(REPO_ROOT / "assets/brand/jaguartv-logo.png")).convert("RGBA")
    logo.thumbnail((170, 170))
    img.paste(logo, (830, 28), logo)
    draw.text((60, 300), "PRÉ-JOGO (dry)", fill=(255, 230, 70))
    img.save(output)


# --- phase 4 (custom per-batch rotation + captions) -------------------------------------
def _phase4(config, run_dir: Path, batch: int, date_seed: str, dry_run: bool) -> dict:
    posters = _read_json(run_dir / "phase3" / "poster-manifest.json").get("items", [])
    phase_dir = run_dir / "phase4"
    if not posters:
        report = {"ok": True, "status": "NO_CONTENT", "video_count": 0}
        _write_json(phase_dir / "build-manifest.json", report)
        return report

    pools = _component_pools(REPO_ROOT)
    rotations = deterministic_batch_rotation(date_seed, [i["task_id"] for i in posters], pools)
    items = []
    for seq, item in enumerate(posters, 1):
        poster = Path(item["poster"])
        names = video_filenames(poster, int(config.data["video"].get("generation_seconds", 4)), seq)
        out = phase_dir / names["media_stem"]
        master = out / names["master"]
        hook = out / names["hook"]
        final = out / names["final"]
        cover = out / names["cover"]
        master_info = make_vertical_master(poster, master)
        from PIL import Image
        Image.open(master).save(cover, "JPEG", quality=92)
        motion_prompt = (
            f"Animate this exact 9:16 JaguarTV pre-match master for 4 seconds: {master.name}. "
            "Keep the poster-cover composition visible as the dominant full-frame subject throughout the "
            "first 3 seconds; preserve all Brazilian Portuguese text, player identity, club kit, crests, "
            "channel icons, date, kickoff time, prediction, and the upper-right JaguarTV logo. Do not add a "
            "second JaguarTV logo. Make the background alive with stadium lights, crowd depth, sparks, cloth "
            "movement, and a fierce face-to-face player confrontation when two players are present."
        )
        motion_path = out / "motion-prompt.txt"
        motion_path.parent.mkdir(parents=True, exist_ok=True)
        motion_path.write_text(motion_prompt, encoding="utf-8")
        if dry_run:
            raw = out / names["raw_video"]
            _loop_video(master, raw, int(config.data["video"].get("generation_seconds", 4)))
            dreamina = {"provider_id": "dry-run", "task_id": None, "raw_video": str(raw)}
        else:
            # download_dreamina_result returns the FIRST *.mp4 found in `out`; clear stale artifacts
            # from previous runs of the same run_dir, or a re-run silently reuses an old hook video
            # whose content no longer matches the freshly generated poster.
            for _stale in out.glob("*.mp4"):
                _stale.unlink()
            task_id = submit_dreamina_hook(master, motion_prompt, config.data["video"])
            raw = download_dreamina_result(task_id, out, config.data["video"].get("dreamina_command", "dreamina"))
            dreamina = {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"],
                        "task_id": task_id, "raw_video": str(raw)}
        make_exact_hook(master, raw, hook)
        compose_v7(REPO_ROOT, master=master, hook=hook, output=final, components=rotations[item["task_id"]])
        _check_duration(final, 12.0)
        items.append({
            "task_id": item["task_id"], "kind": item.get("kind"), "sequence": seq, "poster": str(poster),
            "master": str(master), "hook": str(hook), "final": str(final), "cover": str(cover),
            "cover_source": "full poster master", "motion_prompt": str(motion_path), "master_info": master_info,
            "components": rotations[item["task_id"]], "dreamina": dreamina,
        })
    captions = _captions_for_batch(run_dir, items, batch)
    _write_json(phase_dir / "captions.json", captions)
    manifest = {
        "ok": True, "status": "PHASE4_COMPLETE", "video_count": len(items),
        "date_seed": date_seed, "batch": batch, "items": items,
        "captions": str(phase_dir / "captions.json"),
    }
    _write_json(phase_dir / "build-manifest.json", manifest)
    return manifest


def _captions_for_batch(run_dir: Path, items: list[dict], batch: int) -> dict:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    date_iso = _run_date_iso(run_dir)
    weekday = WEEKDAY_PT[datetime.fromisoformat(date_iso).weekday()]
    date_pt = _date_long_pt(date_iso)
    out_items = []
    for item in items:
        fx = _fixture_for(run_dir, item["task_id"]) or {}
        if item.get("kind") == "schedule" or not fx:
            rows = "; ".join(f"{f['home_team']} x {f['away_team']} ({f['kickoff_at_brt']})"
                             for f in sorted(fixtures, key=lambda r: str(r.get("kickoff_at_brt", ""))))
            hashtags = ["#futebol", "#brasileirao", "#palpites", "#tvaoVivo", "#jaguartvbrasil"]
            out_items.append({"task_id": item["task_id"], "title": f"Agenda de {weekday} na JaguarTV",
                              "description": f"🗓️ Agenda de {weekday}: {rows}. 7 dias grátis no Jaguar TV, "
                              f"TV ao vivo no Android e TV Box! Baixa no jaguartvbrasil.com 📲 {' '.join(hashtags)}",
                              "hashtags": hashtags})
            continue
        home, away = str(fx.get("home_team")), str(fx.get("away_team"))
        channels = " / ".join(fx.get("channels") or ["Jaguar TV"])
        competition = str(fx.get("competition") or "")
        score = _predicted_score(run_dir, item["task_id"])
        tactical = _tactical_point(run_dir, item["task_id"])
        hashtags = _hashtags(home, away, competition)
        opener = "🔥 É HOJE!" if batch == 1 else "⚡ Tá chegando!"
        hook = f"{opener} {home} x {away} às {fx.get('kickoff_at_brt')}, {competition}."
        description = (f"{hook} Palpite JaguarTV: {score}. {tactical} "
                       f"Vem ver ao vivo no Jaguar TV 📺 7 dias grátis no jaguartvbrasil.com, Android e TV Box. "
                       f"{' '.join(hashtags)}").replace("  ", " ").strip()
        out_items.append({"task_id": item["task_id"], "title": f"Palpite JaguarTV: {home} x {away}",
                          "description": description, "hashtags": hashtags})
    return {"schema_version": "jaguartv-prematch-captions-v1", "language": "pt-BR",
            "timezone_label": "Horário de Brasília", "batch": batch, "items": out_items,
            "fixture_count": len(fixtures)}


# --- phase 1 / 2 ----------------------------------------------------------------------
def _phase1(config, run_dir: Path, dry_run: bool, skip: bool, fixtures_file: Path | None = None) -> dict:
    out = run_dir / "phase1"
    if skip and (out / "selected-fixtures.json").is_file():
        return _read_json(out / "selected-fixtures.json")
    cmd = [sys.executable, "-m", "jaguartv_prematch.cli", "collect", "--config", str(config.path),
           "--output", str(out)]
    if fixtures_file:
        cmd += ["--fixtures-file", str(fixtures_file)]
    r = _run(cmd, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"phase1 collect failed: {r.stderr or r.stdout}"[:800])
    return _read_json(out / "selected-fixtures.json")


def _phase2(run_dir: Path, dry_run: bool, research_dir: Path | None) -> dict:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    phase_dir = run_dir / "phase2"
    phase_dir.mkdir(parents=True, exist_ok=True)
    if not fixtures:
        return {"ok": True, "status": "NO_CONTENT", "research_count": 0}
    missing = []
    for fx in fixtures:
        fid = str(fx["fixture_id"])
        candidates = []
        if research_dir:
            candidates.append(research_dir / f"{fid}_research.json")
        candidates.append(phase_dir / f"{fid}_research.json")
        src = next((c for c in candidates if c.is_file()), None)
        if src is None:
            missing.append(fid)
            continue
        # Surface the evidence into phase2/ so downstream phase4 (captions) and phase5
        # readers that load run_dir/phase2/<fid>_research.json actually consume it.
        dst = phase_dir / f"{fid}_research.json"
        if src != dst:
            shutil.copy(src, dst)
    if missing:
        if dry_run:
            for fid in missing:
                _write_json(phase_dir / f"{fid}_research.json", {
                    "schema_version": "jaguartv-prematch-research-v1", "dry_run": True,
                    "fixture_id": fid, "retrieval_time": _now_brt().isoformat(),
                    "source_records": [{"source_url": "", "platform": "bridge", "publication_time": "",
                                         "retrieval_time": _now_brt().isoformat(),
                                         "source_excerpt": "Dry-run bridge evidence only.",
                                         "normalized_summary": "Bridge evidence; not real research.",
                                         "confidence": "dry-run"}],
                })
            print(f"[warn] phase2 bridge research generated for {missing} (dry-run only)", flush=True)
        else:
            raise RuntimeError(f"phase2 blocked: missing current research evidence for {missing}; "
                               f"supply --research-dir or generate real research first")
    return {"ok": True, "status": "PHASE2_COMPLETE", "research_count": len(fixtures)}


# --- phase 5 (upload, excluding the Lark sample) --------------------------------------
def _phase5(config, run_dir: Path, dry_run: bool, exclude_task_ids: set[str] | None = None) -> dict:
    exclude_task_ids = exclude_task_ids or set()
    videos = [
        item for item in _read_json(run_dir / "phase4" / "build-manifest.json").get("items", [])
        if item.get("task_id") not in exclude_task_ids
    ]
    phase_dir = run_dir / "phase5"
    if dry_run:
        result = {"ok": True, "status": "DRY_RUN_NOT_UPLOADED", "upload_count": 0,
                  "excluded_task_ids": sorted(exclude_task_ids),
                  "would_upload": [item["final"] for item in videos]}
        _write_json(phase_dir / "upload-manifest.json", result)
        return result
    publishing = config.data["publishing"]
    uploads = []
    for item in videos:
        upload = upload_pending_review(
            Path(item["final"]),
            _upload_metadata(run_dir, item),
            base_url=config.env_value(publishing, "dashboard_url_env"),
            upload_token=config.env_value(publishing, "upload_token_env"),
            dashboard_token=config.env_value(publishing, "dashboard_token_env"),
        )
        uploads.append({"task_id": item["task_id"], **upload})
    result = {"ok": True, "status": "PHASE5_COMPLETE", "upload_count": len(uploads),
              "excluded_task_ids": sorted(exclude_task_ids), "uploads": uploads}
    _write_json(phase_dir / "upload-manifest.json", result)
    return result


# --- delivery + lark ------------------------------------------------------------------
def _deliver(run_dir: Path, batch: int, captions: dict, exclude_task_ids: set[str] | None = None) -> dict:
    exclude_task_ids = exclude_task_ids or set()
    poster_dst = DESKTOP / f"每日赛前海报{batch}"
    video_dst = DESKTOP / f"赛前预测{batch}"
    poster_dst.mkdir(parents=True, exist_ok=True)
    video_dst.mkdir(parents=True, exist_ok=True)
    copied = {"posters": 0, "videos": 0, "excluded_task_ids": sorted(exclude_task_ids)}
    poster_items = _read_json(run_dir / "phase3" / "poster-manifest.json").get("items", [])
    for item in poster_items:
        if item.get("task_id") in exclude_task_ids:
            continue
        p = Path(item["poster"])
        shutil.copy(p, poster_dst / p.name)
        copied["posters"] += 1
    # Deliver final videos WITHOUT the "final-" prefix: the sequence number must sit
    # at the very front of the filename (e.g. "01桑托斯_vs_..._12s.mp4", not
    # "final-01桑托斯_vs_..._12s.mp4"). The run dir keeps the repo's "final-" convention
    # so phase5/upload stay valid; only the delivered copy is renamed.
    video_items = _read_json(run_dir / "phase4" / "build-manifest.json").get("items", [])
    for item in video_items:
        if item.get("task_id") in exclude_task_ids:
            continue
        v = Path(item["final"])
        dest_name = v.name.replace("final-", "", 1)
        shutil.copy(v, video_dst / dest_name)
        copied["videos"] += 1
    filtered = {**captions, "items": [i for i in captions.get("items", []) if i.get("task_id") not in exclude_task_ids]}
    _write_json(video_dst / "captions.json", filtered)
    copied["captions"] = str(video_dst / "captions.json")
    return {"poster_folder": str(poster_dst), "video_folder": str(video_dst), **copied}


def _lark_msg_id(proc) -> str | None:
    try:
        return json.loads(proc.stdout or "{}").get("data", {}).get("message_id")
    except Exception:
        return None


def _select_lark_task_id(p4: dict) -> str | None:
    items = p4.get("items", [])
    picked = next((i for i in items if i.get("kind") != "schedule"), None) or (items[0] if items else None)
    return str(picked["task_id"]) if picked else None


def _lark_sample(run_dir: Path, captions: dict, dry_run: bool, task_id: str | None = None) -> dict | None:
    items = _read_json(run_dir / "phase4" / "build-manifest.json").get("items", [])
    item = next((i for i in items if not task_id or i.get("task_id") == task_id), None)
    if not item:
        return None
    video = Path(item["final"])
    covers = sorted(video.parent.glob("cover-*.jpg"))
    cover = str(covers[0]) if covers else None
    cap = next((c for c in captions.get("items", []) if c.get("task_id") == item.get("task_id")), {})
    caption_text = cap.get("description", "JaguarTV pré-jogo")
    if dry_run:
        print(f"[dry] lark sample would post {video.name} + caption to group {LARK_GROUP}", flush=True)
        return {"task_id": item.get("task_id"), "dry": True}
    import os as _os
    env = dict(_os.environ)
    env["PATH"] = "/Users/jaguar/.workbuddy/binaries/node/versions/22.22.2-2/bin:" + env.get("PATH", "")
    # lark-cli rejects absolute paths for --video/--file, so run from the media dir with cwd-relative
    # names. Videos require --video + --video-cover (msg_type=media); plain captions use --text.
    if cover:
        r1 = _run([LARK_CLI, "im", "+messages-send", "--chat-id", LARK_GROUP,
                   "--video", video.name, "--video-cover", Path(cover).name],
                 cwd=str(video.parent), env=env, timeout=180)
    else:
        r1 = _run([LARK_CLI, "im", "+messages-send", "--chat-id", LARK_GROUP, "--video", video.name],
                 cwd=str(video.parent), env=env, timeout=180)
    r2 = _run([LARK_CLI, "im", "+messages-send", "--chat-id", LARK_GROUP, "--text", caption_text],
             env=env, timeout=120)
    if r1.returncode != 0 or r2.returncode != 0:
        raise RuntimeError(f"lark sample failed: {(r1.stderr or r1.stdout or r2.stderr or r2.stdout)[:800]}")
    return {"task_id": item.get("task_id"), "file_sent": r1.returncode == 0, "text_sent": r2.returncode == 0,
            "video_msg_id": _lark_msg_id(r1), "text_msg_id": _lark_msg_id(r2)}


# --------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--config", type=Path, default=REPO / "config" / "local.json")
    ap.add_argument("--batch", choices=["auto", "1", "2"], default="auto")
    ap.add_argument("--date", help="YYYYMMDD target (default: tomorrow BRT)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--research-dir", type=Path, default=None)
    ap.add_argument("--skip-collect", action="store_true", help="reuse existing phase1 if present")
    ap.add_argument("--fixtures-file", type=Path, default=None,
                   help="use a local fixtures JSON instead of live collect (resilience fallback)")
    ap.add_argument("--lark-only", action="store_true",
                    help="skip phases 1-5; re-send the existing phase4 video+caption to Lark (validation)")
    args = ap.parse_args()

    batch = int(args.batch) if args.batch != "auto" else _detect_batch()
    date = args.date or _tomorrow_brt_yyyymmdd()
    run_dir = REPO / "runs" / f"{date}_batch{batch}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[info] batch={batch} date={date} run_dir={run_dir} dry_run={args.dry_run}", flush=True)

    _sync_repo()

    if args.lark_only:
        cap_path = run_dir / "phase4" / "captions.json"
        if not cap_path.is_file():
            print(f"[error] no captions.json at {cap_path}; run a full (non --lark-only) batch first", flush=True)
            return 2
        p4 = _read_json(run_dir / "phase4" / "build-manifest.json")
        lark = _lark_sample(run_dir, _read_json(cap_path), args.dry_run, _select_lark_task_id(p4))
        print(json.dumps({"batch": batch, "date": date, "lark_sample": lark}, ensure_ascii=False, indent=2), flush=True)
        return 0

    config = FactoryConfig.load(args.config)

    # 1-2. collect + research (identical/daily-reusable). Spec: steps 1-2 are the SAME for both
    # daily batches, so batch2 reuses batch1's phase1+phase2 instead of re-collecting — this also
    # keeps batch2 working when the live fixtures service is unavailable.
    reuse_b1 = False
    if batch == 2:
        b1_dir = REPO / "runs" / f"{date}_batch1"
        if (b1_dir / "phase1" / "selected-fixtures.json").is_file():
            shutil.copytree(b1_dir / "phase1", run_dir / "phase1", dirs_exist_ok=True)
            if (b1_dir / "phase2").is_dir():
                shutil.copytree(b1_dir / "phase2", run_dir / "phase2", dirs_exist_ok=True)
            reuse_b1 = True
            print("[info] batch2: reusing batch1 phase1+phase2 (spec: steps 1-2 identical)", flush=True)
    _phase1(config, run_dir, args.dry_run, args.skip_collect or reuse_b1, args.fixtures_file)
    _phase2(run_dir, args.dry_run, args.research_dir)

    # 3. style selection + posters (batch-specific style)
    hist = _load_history()
    batch1_style = None
    if batch == 2:
        b1 = run_dir.parent / f"{date}_batch1" / "phase3" / "style-selection.json"
        if b1.is_file():
            batch1_style = _read_json(b1).get("selected_style")
    sel = _select_style(hist, batch, batch1_style)
    style = sel["selected_style"]
    style_scene = _STYLE_SCENE.get(style, "Vertical 9:16 clean floodlit stadium at night, dark green and gold cinematic colour palette.")
    _write_json(run_dir / "phase3" / "style-selection.json", sel)
    print(f"[info] selected style={style} (batch{batch})", flush=True)
    _phase3(config, run_dir, style, style_scene, args.dry_run)

    # 4. video (batch-specific rotation + captions)
    date_seed = f"{date}-B{batch}"
    p4 = _phase4(config, run_dir, batch, date_seed, args.dry_run)
    if not args.dry_run:
        _commit_style(hist, style, hist.get("max_recent_styles", 3))

    # 5. lark sample first; then exclude it from server upload and desktop delivery.
    captions = _read_json(run_dir / "phase4" / "captions.json")
    lark_task_id = _select_lark_task_id(p4)
    lark = _lark_sample(run_dir, captions, args.dry_run, lark_task_id)
    exclude = {lark_task_id} if lark_task_id else set()
    p5 = _phase5(config, run_dir, args.dry_run, exclude)
    delivered = _deliver(run_dir, batch, captions, exclude)

    summary = {
        "batch": batch, "date": date, "run_dir": str(run_dir), "style": style,
        "poster_count": p4.get("poster_count") or len(p4.get("items", [])),
        "video_count": p4.get("video_count", 0),
        "upload": p5.get("status"), "delivered": delivered, "lark_sample": lark,
    }
    _write_json(run_dir / "automation-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


# Style scenes steer MOOD/GRADING ONLY. They must never contain "no text"/"no people" clauses —
# the poster prompts require full pt-BR copy and star players, and such clauses previously
# poisoned the schedule prompt into background-only output.
_STYLE_SCENE = {
    "broadcast_green_gold": "Vertical floodlit stadium at night, dark green pitch, golden smoke and light rays, dark green and gold cinematic colour palette.",
    "cinematic_night": "Dramatic low-key night grade, deep teal/blue, single hard key light raking across wet turf, long shadows, volumetric haze.",
    "stadium_collage": "Street-collage of candid stadium atmosphere, torn-poster texture, bold club-colour blocks framing the edges, mixed crowd and mosaic fragments, grainy print feel.",
    "epic_symbolic": "Monumental low-angle composition, club flags, architectural stadium depth, storm light, warm backlight, restrained sparks, heroic mood.",
    "retro_print_grunge": "Vintage football-print look, warm paper tone, halftone grain, distressed edges, faded newsprint colour, soft vignette.",
    "neon_editorial": "Glossy neon sports-magazine grade, magenta/cyan rim glow, high-contrast club colours, sleek reflective floor, modern editorial typographic grid.",
}


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # stop & report on any integration failure
        print(json.dumps({"ok": False, "blocked": True, "sanitized_error": str(exc)[:800]}, ensure_ascii=False))
        raise SystemExit(2)
