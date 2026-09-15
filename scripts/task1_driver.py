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
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# --- make the repository importable ------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO / "src"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jaguartv_prematch.config import FactoryConfig  # noqa: E402
from jaguartv_prematch.collector import resolve_target_date  # noqa: E402
from jaguartv_prematch.image2 import (  # noqa: E402
    generate_image2,
    save_image_route_manifest,
)
from jaguartv_prematch.pipeline import (  # noqa: E402
    ANTI_FIGURE,
    CREST_SAFETY,
    ROOT as REPO_ROOT,
    WEEKDAY_PT,
    _check_duration,
    _colors,
    _component_pools,
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
from jaguartv_prematch.poster import compose_poster, prepare_background  # noqa: E402
from jaguartv_prematch.upload import UploadError, upload_pending_review  # noqa: E402
from jaguartv_prematch.video import (  # noqa: E402
    compose_v7,
    deterministic_batch_rotation,
    deterministic_motion_plan,
    download_dreamina_result,
    generate_apimart_hook,
    make_exact_hook,
    make_layered_master,
    submit_dreamina_hook,
    validate_layered_hook,
    video_filenames,
    VideoGenerationError,
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


def _target_yyyymmdd(value: str | None) -> str:
    return resolve_target_date(value or "tomorrow", _now_brt()).replace("-", "")


def _summary_complete(run_dir: Path) -> bool:
    summary = run_dir / "automation-summary.json"
    if not summary.is_file():
        return False
    try:
        payload = _read_json(summary)
    except Exception:
        return False
    return payload.get("upload") == "PHASE5_COMPLETE"


def _completed_batches(date: str) -> list[int]:
    batches = []
    for path in sorted((REPO / "runs").glob(f"{date}_batch*")):
        suffix = path.name.removeprefix(f"{date}_batch")
        if suffix.isdigit() and _summary_complete(path):
            batches.append(int(suffix))
    return batches


def _detect_batch(date: str) -> int:
    completed = set(_completed_batches(date))
    batch = 1
    while batch in completed:
        batch += 1
    return batch


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def _sync_repo() -> None:
    if os.environ.get("JAGUARTV_SKIP_REPO_SYNC") == "1":
        print("[warn] repo sync skipped by JAGUARTV_SKIP_REPO_SYNC=1", flush=True)
        return
    r = _run(["git", "-C", str(REPO), "pull", "--rebase", "origin", "main"], timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"repo sync failed: {r.stderr or r.stdout}"[:800])


def _valid_media(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _load_phase4_resume_items(phase_dir: Path) -> list[dict]:
    for name in ("build-manifest.json", "build-manifest.partial.json"):
        path = phase_dir / name
        if not path.is_file():
            continue
        try:
            payload = _read_json(path)
        except Exception:
            continue
        items = []
        for item in payload.get("items", []):
            final = Path(str(item.get("final", "")))
            hook = Path(str(item.get("hook", "")))
            master = Path(str(item.get("master", "")))
            if _valid_media(final) and _valid_media(hook) and _valid_media(master):
                items.append(item)
        if items:
            return items
    return []


def _is_retryable_video_error(error: Exception) -> bool:
    message = str(error).lower()
    retryable = (
        "ret=1015", "cloudflare", "timeout", "timed out", "deadline", "incompleteread",
        "connection", "temporarily", "too many requests", "429", "502", "503", "504",
        "upload image", "upload phase", "no file upload", "without a downloadable mp4",
    )
    return any(token in message for token in retryable)


def _existing_generated_raw(out: Path, task_id: str | None = None) -> Path | None:
    candidates = []
    for directory in (out / "apimart-raw", out / "dreamina-raw", out):
        if directory.is_dir():
            candidates.extend(directory.glob("*.mp4"))
    raw_like = [path for path in candidates if not path.name.startswith(("hook-", "final-")) and _valid_media(path)]
    if task_id:
        task_named = [path for path in raw_like if task_id in path.name]
        if task_named:
            return sorted(task_named, key=lambda path: path.stat().st_mtime, reverse=True)[0]
    if raw_like:
        return sorted(raw_like, key=lambda path: path.stat().st_mtime, reverse=True)[0]
    return None


def _is_duplicate_upload_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(token in message for token in ("already exists", "same video", "duplicate"))


def _is_request_id_reuse_error(error: Exception) -> bool:
    message = str(error).lower()
    return "request_id" in message and "already used" in message


def _pending_review_existing_task_ids(base_url: str, dashboard_token: str) -> set[str]:
    try:
        url = base_url.rstrip("/")
        response = requests.get(
            f"{url}/api/originals",
            params={"status": "PENDING_REVIEW", "category": "pre_match_prediction"},
            headers={"Authorization": f"Bearer {dashboard_token}"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"[warn] pending-review scan skipped: {str(exc)[:240]}", flush=True)
        return set()
    rows = payload.get("items") or payload.get("data") or payload.get("records") or []
    task_ids = set()
    for row in rows:
        detail = row
        upload_id = row.get("id") or row.get("upload_id") or row.get("original_id")
        if upload_id:
            try:
                detail_response = requests.get(
                    f"{url}/api/originals/{upload_id}",
                    headers={"Authorization": f"Bearer {dashboard_token}"},
                    timeout=30,
                )
                if detail_response.ok:
                    detail = detail_response.json()
            except Exception:
                detail = row
        match_info = detail.get("match_info") or detail.get("metadata", {}).get("match_info") or {}
        task_id = match_info.get("task_id")
        if task_id:
            task_ids.add(str(task_id))
    return task_ids


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


def _styles_used_for_date(date: str) -> list[str]:
    used = []
    for path in sorted((REPO / "runs").glob(f"{date}_batch*/phase3/style-selection.json")):
        try:
            style = _read_json(path).get("selected_style")
        except Exception:
            style = None
        if style and style not in used:
            used.append(str(style))
    return used


def _select_style(hist: dict, batch: int, date: str) -> dict:
    pool = hist.get("pool", [])
    recent = hist.get("recent_styles", [])
    used_today = _styles_used_for_date(date)
    candidates = [s for s in pool if s not in used_today and s not in recent]
    if not candidates:
        candidates = [s for s in pool if s not in used_today]
    if not candidates:
        raise RuntimeError(
            f"no unused poster style remains for {date}: used={used_today}; "
            "extend prematch style pool before producing another differentiated batch"
        )
    seed = int(hashlib.sha256(f"{date}:batch{batch}:{','.join(pool)}".encode()).hexdigest(), 16)
    chosen = candidates[seed % len(candidates)]
    return {
        "eligible_styles": candidates,
        "excluded_recent_styles": recent,
        "excluded_same_day_styles": used_today,
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
    """Image2 background prompt; factual foreground is composited deterministically."""
    home, away = str(fixture["home_team"]), str(fixture["away_team"])
    p_home = home_stars[0] if home_stars else "an anonymous hardman footballer representing the home team"
    p_away = away_stars[0] if away_stars else "an anonymous hardman footballer representing the away team"
    p_home_b = home_stars[1] if len(home_stars) > 1 else p_home
    p_away_b = away_stars[1] if len(away_stars) > 1 else p_away
    return (
        "Create a premium 4:5 pre-match football background with no text or graphics. "
        f"Visual mood: {style_scene} "
        f"Players: photorealistic likeness of {p_home} and {p_home_b} in {home} current official kit "
        f"({_colors(home)}) framing the far LEFT edge, and {p_away} and {p_away_b} in {away} current "
        f"official kit ({_colors(away)}) framing the far RIGHT edge, waist-up, facing each other; they "
        "must leave the top 30%, central information band, and bottom 38% visually quiet. "
        "Absolutely no readable text, digits, typography, UI panels, scoreboards, logos, crests, "
        "channel marks, sponsor marks, watermarks, or JaguarTV imagery. "
        f"{CREST_SAFETY} {ANTI_FIGURE}"
    )


def _schedule_poster_prompt(fixtures: list[dict], style_scene: str) -> str:
    return (
        "Create a premium 4:5 football schedule background only. "
        f"Visual mood: {style_scene} "
        "Use stadium depth and restrained club-neutral lighting with quiet space for a header and up to "
        "eight factual rows. Absolutely no readable text, digits, typography, row labels, UI panels, "
        "logos, crests, channel marks, sponsor marks, watermarks, or JaguarTV imagery."
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
    dated = sorted(fixtures, key=lambda item: str(item.get("kickoff_at_brt", "")))
    for offset in range(0, len(dated), 8):
        page = offset // 8 + 1
        suffix = f"-{page:02d}" if len(dated) > 8 else ""
        tasks.append({
            "id": f"schedule-{_yyMMdd(str(dated[0]['schedule_date']))}{suffix}", "kind": "schedule",
            "filename": f"{_yyMMdd(str(dated[0]['schedule_date']))}_赛程海报{suffix}.png",
            "prompt": _schedule_poster_prompt(dated[offset : offset + 8], style_scene),
            "title": "Agenda JaguarTV", "fixtures": dated[offset : offset + 8],
        })

    existing_items = {}
    manifest_path = phase_dir / "poster-manifest.json"
    if manifest_path.is_file():
        try:
            existing_items = {str(i.get("task_id")): i for i in _read_json(manifest_path).get("items", [])}
        except Exception:
            existing_items = {}

    items = []
    total = len(tasks)
    for index, task in enumerate(tasks, 1):
        prompt_path = phase_dir / "prompts" / f"{task['id']}.txt"
        raw_path = phase_dir / "raw" / f"{task['id']}.png"
        background_path = phase_dir / "backgrounds" / f"{task['id']}.png"
        foreground_path = phase_dir / "foregrounds" / f"{task['id']}.png"
        poster_path = phase_dir / "posters" / task["filename"]
        existing = existing_items.get(str(task["id"]))
        if existing and _valid_media(Path(str(existing.get("poster", "")))):
            print(f"[phase3] {index}/{total} poster resume: {poster_path.name}", flush=True)
            items.append(existing)
            continue
        print(f"[phase3] {index}/{total} image2 poster start: {task['id']}", flush=True)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(task["prompt"], encoding="utf-8")
        if dry_run:
            _make_dry_poster(raw_path)
            route = [{"provider_id": "dry-run", "model_id": "none", "status": "not_called"}]
        else:
            attempts = generate_image2(task["prompt"], raw_path, config.data["image"])
            save_image_route_manifest(phase_dir / "image-routes" / f"{task['id']}.json", attempts)
            route = [a.__dict__ for a in attempts]
        prepare_background(raw_path, background_path)
        task_fixtures = task["fixtures"] if task["kind"] == "schedule" else [next(fx for fx in fixtures if _task_id(fx) == task["id"])]
        predictions = {_task_id(fx): _prediction_block(fx, run_dir) for fx in task_fixtures}
        compose_meta = compose_poster(
            background_path, poster_path, foreground_path, kind=task["kind"],
            fixtures=task_fixtures, predictions=predictions,
            logo_path=REPO_ROOT / "assets" / "brand" / "jaguartv-logo.png",
            channels_root=REPO_ROOT / "assets" / "channels",
        )
        items.append({
            "task_id": task["id"], "kind": task["kind"], "prompt": str(prompt_path),
            "raw_background": str(raw_path), "background": str(background_path),
            "foreground": str(foreground_path), "poster": str(poster_path),
            "compose": compose_meta, "image_route": route,
        })
        _write_json(manifest_path, {
            "ok": True, "status": "PHASE3_IN_PROGRESS", "poster_count": len(items),
            "expected_poster_count": total, "items": items,
        })
        print(f"[phase3] {index}/{total} poster complete: {poster_path.name}", flush=True)
    manifest = {"ok": True, "status": "PHASE3_COMPLETE", "poster_count": len(items), "items": items}
    _write_json(phase_dir / "poster-manifest.json", manifest)
    return manifest


def _make_dry_poster(output: Path) -> None:
    from PIL import Image, ImageDraw
    output.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1024, 1280), (12, 92, 48))
    draw = ImageDraw.Draw(img)
    draw.ellipse((80, 180, 930, 1100), fill=(18, 120, 78))
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
    motion_plan = deterministic_motion_plan([i["task_id"] for i in posters], date_seed)
    resume_items = {str(i.get("task_id")): i for i in _load_phase4_resume_items(phase_dir)}
    items = []
    total = len(posters)
    for seq, item in enumerate(posters, 1):
        poster = Path(item["poster"])
        names = video_filenames(poster, int(config.data["video"].get("generation_seconds", 4)), seq)
        out = phase_dir / names["media_stem"]
        master = out / names["master"]
        background_master = out / f"background-{names['master']}"
        foreground_master = out / f"foreground-{names['master']}"
        hook = out / names["hook"]
        final = out / names["final"]
        cover = out / names["cover"]
        existing = resume_items.get(str(item["task_id"]))
        if existing and _valid_media(Path(str(existing.get("final", "")))):
            print(f"[phase4] {seq}/{total} video resume: {Path(existing['final']).name}", flush=True)
            items.append(existing)
            continue
        print(f"[phase4] {seq}/{total} video start: {poster.name}", flush=True)
        master_info = make_layered_master(
            Path(item["background"]), Path(item["foreground"]), master,
            background_master, foreground_master,
        )
        from PIL import Image
        Image.open(master).save(cover, "JPEG", quality=92)
        motion_prompt = (
            f"Animate only this text-free 9:16 football background for exactly 4 seconds: {background_master.name}. "
            "Use stadium lights, crowd depth, restrained sparks and cloth movement. Do not add text, scores, "
            "team graphics, channel icons, logos, watermarks, UI, or new people. The factual foreground is locked "
            "and composited locally after generation."
        )
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
            dreamina_state = out / "dreamina-submit.json"
            task_id = None
            if dreamina_state.is_file():
                try:
                    task_id = str(_read_json(dreamina_state).get("task_id") or "") or None
                except Exception:
                    task_id = None
            raw = _existing_generated_raw(out, task_id)
            if raw:
                if raw.parent.name == "apimart-raw":
                    fallback = config.data["video"]["fallback"]
                    generation = {"provider_id": fallback["provider_id"], "model_id": fallback["model_id"],
                                  "task_id": None, "raw_video": str(raw), "reused_raw": True, "status": "ok",
                                  "fallback_from": config.data["video"]["provider_id"]}
                else:
                    generation = {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"],
                                  "task_id": task_id, "raw_video": str(raw), "reused_raw": True, "status": "ok"}
                print(f"[phase4] {seq}/{total} generated raw resume: {Path(raw).name}", flush=True)
            else:
                last_error = None
                try:
                    for attempt in range(1, 5):
                        try:
                            raw_dir = out / "dreamina-raw"
                            if not task_id:
                                # Keep Dreamina query/download artifacts isolated. Some CLI versions clean or
                                # rewrite the download_dir; using `out` directly can remove master/cover files
                                # needed by ffmpeg immediately after polling succeeds.
                                if raw_dir.is_dir():
                                    shutil.rmtree(raw_dir)
                                raw_dir.mkdir(parents=True, exist_ok=True)
                                print(f"[phase4] {seq}/{total} submit dreamina background: {background_master.name}", flush=True)
                                task_id = submit_dreamina_hook(background_master, motion_prompt, config.data["video"])
                                _write_json(dreamina_state, {
                                    "provider_id": config.data["video"]["provider_id"],
                                    "model_id": config.data["video"]["model_id"],
                                    "task_id": task_id,
                                    "raw_dir": str(raw_dir),
                                    "submitted_at": _now_brt().isoformat(),
                                })
                            print(f"[phase4] {seq}/{total} poll dreamina: {task_id}", flush=True)
                            raw = download_dreamina_result(task_id, raw_dir, config.data["video"].get("dreamina_command", "dreamina"))
                            generation = {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"],
                                          "task_id": task_id, "raw_video": str(raw), "status": "ok"}
                            print(f"[phase4] {seq}/{total} dreamina downloaded: {Path(raw).name}", flush=True)
                            break
                        except VideoGenerationError as exc:
                            last_error = exc
                            if not _is_retryable_video_error(exc) or attempt >= 4:
                                raise
                            task_id = None
                            if dreamina_state.is_file():
                                dreamina_state.unlink()
                            wait_seconds = 20 * attempt
                            print(f"[phase4] {seq}/{total} dreamina transient error, retry {attempt}/4 after {wait_seconds}s: {exc}", flush=True)
                            time.sleep(wait_seconds)
                    else:
                        raise last_error or RuntimeError("dreamina generation failed")
                except VideoGenerationError as primary_error:
                    fallback = config.data["video"].get("fallback")
                    if not fallback:
                        raise
                    fallback_raw = out / "apimart-raw" / f"apimart-{names['media_stem']}-4s.mp4"
                    print(f"[phase4] {seq}/{total} dreamina unavailable; using configured APIMart fallback", flush=True)
                    generation = generate_apimart_hook(background_master, motion_prompt, fallback, fallback_raw)
                    generation["fallback_from"] = config.data["video"]["provider_id"]
                    generation["attempts"] = [
                        {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"],
                         "status": "failed", "sanitized_error": str(primary_error)[:500]},
                        {"provider_id": generation["provider_id"], "model_id": generation["model_id"], "status": "ok"},
                    ]
                    raw = fallback_raw
        if selected_for_motion:
            assert raw is not None
            make_exact_hook(master, Path(raw), foreground_master, hook, 4.0)
        layer_qa = validate_layered_hook(hook, master, foreground_master)
        if not layer_qa["foreground_stable"]:
            raise RuntimeError(f"locked foreground changed during hook for {item['task_id']}: {layer_qa}")
        final_seconds = compose_v7(REPO_ROOT, master=master, hook=hook, output=final, components=rotations[item["task_id"]])
        _check_duration(final, final_seconds)
        items.append({
            "task_id": item["task_id"], "kind": item.get("kind"), "sequence": seq, "poster": str(poster),
            "master": str(master), "hook": str(hook), "final": str(final), "cover": str(cover),
            "cover_source": "full poster master", "motion_prompt": str(motion_path), "master_info": master_info,
            "generated_seconds": 4, "final_seconds": round(final_seconds, 3),
            "motion_selection": motion_plan[item["task_id"]], "layer_qa": layer_qa,
            "background_master": str(background_master), "foreground_master": str(foreground_master),
            "components": rotations[item["task_id"]], "video_generation": generation,
        })
        _write_json(phase_dir / "build-manifest.partial.json", {
            "ok": True, "status": "PHASE4_IN_PROGRESS", "video_count": len(items),
            "expected_video_count": total, "date_seed": date_seed, "batch": batch, "items": items,
        })
        print(f"[phase4] {seq}/{total} final complete: {final.name}", flush=True)
    captions = _captions_for_batch(run_dir, items, batch)
    _write_json(phase_dir / "captions.json", captions)
    manifest = {
        "ok": True, "status": "PHASE4_COMPLETE", "video_count": len(items),
        "date_seed": date_seed, "batch": batch, "items": items,
        "captions": str(phase_dir / "captions.json"),
    }
    _write_json(phase_dir / "build-manifest.json", manifest)
    return manifest


def _caption_rng(run_dir: Path, batch: int, task_id: str) -> random.Random:
    return random.Random(hashlib.sha256(f"{run_dir.name}:batch{batch}:{task_id}:captions".encode()).hexdigest())


def _caption_choice(run_dir: Path, batch: int, task_id: str, values: list[str]) -> str:
    return values[_caption_rng(run_dir, batch, task_id).randrange(len(values))]


def _captions_for_batch(run_dir: Path, items: list[dict], batch: int) -> dict:
    selected = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    fixtures = selected.get("fixtures", [])
    date_iso = _run_date_iso(run_dir)
    weekday = WEEKDAY_PT[datetime.fromisoformat(date_iso).weekday()]
    out_items = []
    schedule_templates = [
        "🗓️ Agenda de {weekday}: {rows}. TV ao vivo no Jaguar TV para Android e TV Box. {download} {hashtags}",
        "📺 Guia JaguarTV de {weekday}: {rows}. Escolhe teu jogo e acompanha no Android ou TV Box. {download} {hashtags}",
        "🔥 Programação pronta para {weekday}: {rows}. Tudo no Jaguar TV, com app para Android e TV Box. {download} {hashtags}",
        "⚽ Cola na agenda de {weekday}: {rows}. Pré-jogo, bola rolando e TV ao vivo no Jaguar TV. {download} {hashtags}",
    ]
    match_templates = [
        "🔥 É HOJE! {home} x {away} às {kickoff}, {competition}. Palpite JaguarTV: {score}. {tactical} Vem ver ao vivo no Jaguar TV 📺 {download} {hashtags}",
        "⚡ Tá chegando! {home} x {away} entra em campo às {kickoff} por {competition}. Meu palpite: {score}. {tactical} Assiste no Jaguar TV. {download} {hashtags}",
        "👀 Jogo com cara de tensão: {home} x {away}, {kickoff}, {competition}. Palpite JaguarTV: {score}. {tactical} Acompanha ao vivo no Jaguar TV. {download} {hashtags}",
        "🚨 Anota esse confronto: {home} x {away} às {kickoff}, {competition}. Placar projetado: {score}. {tactical} Jaguar TV no Android e TV Box. {download} {hashtags}",
        "🎯 Pré-jogo JaguarTV: {home} x {away}, {kickoff}, {competition}. Palpite do dia: {score}. {tactical} Quer ver ao vivo? {download} {hashtags}",
        "📌 Fica de olho: {home} x {away} às {kickoff}, {competition}. Leitura JaguarTV: {score}. {tactical} {download} {hashtags}",
    ]
    for item in items:
        task_id = str(item["task_id"])
        fx = _fixture_for(run_dir, task_id) or {}
        if item.get("kind") == "schedule" or not fx:
            rows = "; ".join(f"{f['home_team']} x {f['away_team']} ({f['kickoff_at_brt']})"
                             for f in sorted(fixtures, key=lambda r: str(r.get("kickoff_at_brt", ""))))
            hashtags = ["#futebol", "#brasileirao", "#palpites", "#jaguartv", "#iptv"]
            template = _caption_choice(run_dir, batch, task_id, schedule_templates)
            out_items.append({"task_id": task_id, "title": f"Agenda de {weekday} na JaguarTV",
                              "description": template.format(weekday=weekday, rows=rows, download="Acesse jaguartvbrasil.com/baixar-app para baixar.", hashtags=" ".join(hashtags)),
                              "hashtags": hashtags})
            continue
        home, away = str(fx.get("home_team")), str(fx.get("away_team"))
        competition = str(fx.get("competition") or "")
        score = _predicted_score(run_dir, task_id)
        tactical = _tactical_point(run_dir, task_id)
        hashtags = _hashtags(home, away, competition)
        template = _caption_choice(run_dir, batch, task_id, match_templates)
        description = template.format(
            home=home,
            away=away,
            kickoff=fx.get("kickoff_at_brt"),
            competition=competition,
            score=score,
            tactical=tactical,
            download="Acesse jaguartvbrasil.com/baixar-app para baixar.",
            hashtags=" ".join(hashtags),
        ).replace("  ", " ").strip()
        out_items.append({"task_id": task_id, "title": f"Palpite JaguarTV: {home} x {away}",
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
           "--output", str(out), "--date", _run_date_iso(run_dir)]
    if fixtures_file:
        cmd += ["--fixtures-file", str(fixtures_file)]
    r = _run(cmd, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"phase1 collect failed: {r.stderr or r.stdout}"[:800])
    selected = _read_json(out / "selected-fixtures.json")
    target_date = _run_date_iso(run_dir)
    wrong_dates = sorted({
        str(fixture.get("schedule_date"))
        for fixture in selected.get("fixtures", [])
        if str(fixture.get("schedule_date")) != target_date
    })
    if wrong_dates:
        raise RuntimeError(f"phase1 date mismatch: target={target_date} returned={wrong_dates}")
    return selected


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
    base_url = config.env_value(publishing, "dashboard_url_env")
    upload_token = config.env_value(publishing, "upload_token_env")
    dashboard_token = config.env_value(publishing, "dashboard_token_env")
    already_uploaded = set()
    existing_manifest = phase_dir / "upload-manifest.json"
    if existing_manifest.is_file():
        try:
            for row in _read_json(existing_manifest).get("uploads", []):
                if row.get("task_id"):
                    already_uploaded.add(str(row["task_id"]))
        except Exception:
            already_uploaded = set()
    already_uploaded |= _pending_review_existing_task_ids(base_url, dashboard_token)
    uploads = []
    for item in videos:
        task_id = str(item["task_id"])
        if task_id in already_uploaded:
            uploads.append({"task_id": task_id, "already_exists": True, "status": "PENDING_REVIEW"})
            continue
        last_error = None
        for attempt in range(1, 5):
            metadata = _upload_metadata(run_dir, item)
            if attempt > 1:
                metadata.setdefault("metadata", {})["req_nonce"] = f"{run_dir.name}-{task_id}-{attempt}-{uuid.uuid4().hex[:8]}"
            try:
                upload = upload_pending_review(
                    Path(item["final"]),
                    metadata,
                    base_url=base_url,
                    upload_token=upload_token,
                    dashboard_token=dashboard_token,
                )
                uploads.append({"task_id": task_id, **upload})
                break
            except UploadError as exc:
                last_error = exc
                if _is_duplicate_upload_error(exc):
                    uploads.append({"task_id": task_id, "already_exists": True, "status": "PENDING_REVIEW"})
                    break
                if not _is_request_id_reuse_error(exc) or attempt >= 4:
                    raise
                print(f"[phase5] request_id reused for {task_id}, retry with nonce {attempt}/4", flush=True)
        else:
            raise last_error or RuntimeError(f"upload failed for {task_id}")
        _write_json(phase_dir / "upload-manifest.json", {
            "ok": True, "status": "PHASE5_IN_PROGRESS", "upload_count": len(uploads),
            "excluded_task_ids": sorted(exclude_task_ids), "uploads": uploads,
        })
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
    # Deliver a plain, copy-paste-ready pt-BR caption draft (no numbering, no filenames,
    # no field labels) instead of the raw captions.json. The full captions.json remains in
    # the run dir (phase4/captions.json) for the record; only the clean draft ships to desktop.
    filtered = {**captions, "items": [i for i in captions.get("items", []) if i.get("task_id") not in exclude_task_ids]}
    copy_text = "\n\n".join(i.get("description", "").strip() for i in filtered.get("items", []))
    copy_path = video_dst / "文案稿.txt"
    copy_path.write_text((copy_text + "\n") if copy_text else "", encoding="utf-8")
    copied["copy_text"] = str(copy_path)
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


def _lark_send(command: list[str], **kwargs):
    result = None
    for attempt in range(1, 4):
        result = _run(command, **kwargs)
        if result.returncode == 0:
            return result
        if attempt < 3:
            print(f"[lark] send failed; retry {attempt}/3", flush=True)
            time.sleep(10 * attempt)
    return result


def _lark_sample(run_dir: Path, captions: dict, dry_run: bool, task_id: str | None = None,
                 *, force: bool = False) -> dict | None:
    items = _read_json(run_dir / "phase4" / "build-manifest.json").get("items", [])
    item = next((i for i in items if not task_id or i.get("task_id") == task_id), None)
    if not item:
        return None
    # The group sample must reach copa运营群 exactly once. Without this record any replay of the
    # run (a resumed phase5, an additive re-run, an operator retry) silently posted a second
    # copy of the same video to the group. `--lark-only` passes force=True to re-send on purpose.
    record_path = run_dir / "phase4" / "lark-sample.json"
    if not force and not dry_run and record_path.is_file():
        try:
            recorded = _read_json(record_path)
        except Exception:
            recorded = None
        if isinstance(recorded, dict) and recorded.get("file_sent") and recorded.get("text_sent"):
            print(f"[lark] sample already delivered for {recorded.get('task_id')}; skipping duplicate send", flush=True)
            return recorded
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
    # lark-cli rejects absolute paths for --video/--file, so send from an isolated
    # cwd-relative staging copy. This keeps the production phase4 directory immutable even
    # if the CLI validates, rewrites, or cleans files during upload.
    lark_dir = run_dir / "phase4" / "lark-sample-staging"
    if lark_dir.is_dir():
        shutil.rmtree(lark_dir)
    lark_dir.mkdir(parents=True, exist_ok=True)
    staged_video = lark_dir / video.name
    shutil.copy(video, staged_video)
    if cover:
        staged_cover = lark_dir / Path(cover).name
        shutil.copy(cover, staged_cover)
        r1 = _lark_send([LARK_CLI, "im", "+messages-send", "--chat-id", LARK_GROUP,
                         "--video", staged_video.name, "--video-cover", staged_cover.name],
                        cwd=str(lark_dir), env=env, timeout=180)
    else:
        r1 = _lark_send([LARK_CLI, "im", "+messages-send", "--chat-id", LARK_GROUP, "--video", staged_video.name],
                        cwd=str(lark_dir), env=env, timeout=180)
    r2 = _lark_send([LARK_CLI, "im", "+messages-send", "--chat-id", LARK_GROUP, "--text", caption_text],
                    env=env, timeout=120)
    if r1.returncode != 0 or r2.returncode != 0:
        raise RuntimeError(f"lark sample failed: {(r1.stderr or r1.stdout or r2.stderr or r2.stdout)[:800]}")
    result = {"task_id": item.get("task_id"), "file_sent": r1.returncode == 0, "text_sent": r2.returncode == 0,
              "video_msg_id": _lark_msg_id(r1), "text_msg_id": _lark_msg_id(r2),
              "sent_at": _now_brt().isoformat()}
    _write_json(record_path, result)
    return result


# --------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--config", type=Path, default=REPO / "config" / "local.json")
    ap.add_argument("--batch", default="auto", help="auto or a positive batch number")
    ap.add_argument("--date", help="today, tomorrow, YYYY-MM-DD, or YYYYMMDD (default: tomorrow BRT)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--research-dir", type=Path, default=None)
    ap.add_argument("--skip-collect", action="store_true", help="reuse existing phase1 if present")
    ap.add_argument("--fixtures-file", type=Path, default=None,
                   help="use a local fixtures JSON instead of live collect (resilience fallback)")
    ap.add_argument("--lark-only", action="store_true",
                    help="skip phases 1-5; re-send the existing phase4 video+caption to Lark (validation)")
    args = ap.parse_args()

    date = _target_yyyymmdd(args.date)
    if args.batch == "auto":
        batch = _detect_batch(date)
    else:
        try:
            batch = int(args.batch)
        except ValueError as exc:
            raise RuntimeError(f"invalid --batch value: {args.batch}; expected auto or a positive integer") from exc
        if batch < 1:
            raise RuntimeError(f"invalid --batch value: {args.batch}; expected auto or a positive integer")
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
        lark = _lark_sample(run_dir, _read_json(cap_path), args.dry_run, _select_lark_task_id(p4), force=True)
        print(json.dumps({"batch": batch, "date": date, "lark_sample": lark}, ensure_ascii=False, indent=2), flush=True)
        return 0

    config = FactoryConfig.load(args.config)

    # 1-2. collect + research are daily-reusable. Later differentiated batches reuse the
    # earliest same-day phase1/phase2 instead of re-collecting.
    reused_daily = False
    if batch > 1:
        for previous in range(1, batch):
            previous_dir = REPO / "runs" / f"{date}_batch{previous}"
            if (previous_dir / "phase1" / "selected-fixtures.json").is_file():
                shutil.copytree(previous_dir / "phase1", run_dir / "phase1", dirs_exist_ok=True)
                if (previous_dir / "phase2").is_dir():
                    shutil.copytree(previous_dir / "phase2", run_dir / "phase2", dirs_exist_ok=True)
                reused_daily = True
                print(f"[info] batch{batch}: reusing batch{previous} phase1+phase2 (daily steps identical)", flush=True)
                break
    _phase1(config, run_dir, args.dry_run, args.skip_collect or reused_daily, args.fixtures_file)
    _phase2(run_dir, args.dry_run, args.research_dir)

    # 3. style selection + posters (same-day batches must use different styles)
    hist = _load_history()
    sel = _select_style(hist, batch, date)
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
