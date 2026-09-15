from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from .credentials import resolve_base_url, resolve_secret
from .retry import _write_state, retry_forever
from .runtime import resolve_command


class VideoGenerationError(RuntimeError):
    pass


def _is_terminal_status(payload: dict[str, Any]) -> bool:
    status = str(payload.get("gen_status") or payload.get("status") or "").lower()
    return status in {"success", "succeeded", "finished", "completed", "failed", "fail", "error"}


def _payload_video_url(payload: dict[str, Any]) -> str:
    result = payload.get("result_json") if isinstance(payload.get("result_json"), dict) else payload
    videos = result.get("videos") if isinstance(result, dict) else None
    if isinstance(videos, list) and videos:
        for item in videos:
            if isinstance(item, dict) and item.get("video_url"):
                return str(item["video_url"])
    return ""


def _terminal_reason(payload: dict[str, Any]) -> str:
    status = payload.get("gen_status") or payload.get("status") or "unknown"
    fail = payload.get("fail_reason") or payload.get("error") or ""
    return f"status={status} fail_reason={fail}".strip()


def video_filenames(poster_path: str | Path, source_seconds: int = 4, sequence: int | None = None) -> dict[str, str]:
    stem = _safe(Path(poster_path).stem)
    if sequence is not None:
        stem = f"{sequence:02d}{stem}"
    return {
        "media_stem": stem,
        "master": f"master-{stem}-1080x1920.png",
        "raw_video": f"jimeng-{stem}-{source_seconds}s.mp4",
        "hook": f"hook-{stem}-4s.mp4",
        "final": f"final-{stem}.mp4",
        "cover": f"cover-{stem}-1080x1920.jpg",
    }


def deterministic_rotation(date_brt: str, fixture_id: str, pools: dict[str, list[str]]) -> dict[str, str]:
    seed = int(hashlib.sha256(f"{date_brt}:{fixture_id}".encode()).hexdigest(), 16)
    selection: dict[str, str] = {}
    offset = 0
    for category in ("operation", "interface", "cta", "music", "voice"):
        values = pools.get(category, [])
        if not values:
            raise VideoGenerationError(f"Empty component pool: {category}")
        selection[category] = values[(seed + offset) % len(values)]
        offset += 1
    return selection


def deterministic_batch_rotation(
    date_brt: str,
    fixture_ids: list[str],
    pools: dict[str, list[str]],
) -> dict[str, dict[str, str]]:
    categories = ("operation", "interface", "cta", "music", "voice")
    for category in categories:
        if not pools.get(category):
            raise VideoGenerationError(f"Empty component pool: {category}")
    middle_pairs = [(a, b) for a in pools["operation"] for b in pools["interface"] if a != b]
    if not middle_pairs:
        raise VideoGenerationError("Component pools need at least two distinct operation-class videos")
    other_combinations = list(itertools.product(pools["cta"], pools["music"], pools["voice"]))
    combinations = list(itertools.product(middle_pairs, other_combinations))
    if len(fixture_ids) > len(combinations):
        raise VideoGenerationError("Component pools cannot provide unique same-day combinations")

    rng = random.Random(hashlib.sha256(date_brt.encode()).hexdigest())
    shuffled_fixture_ids = list(fixture_ids)
    shuffled_combinations = list(combinations)
    rng.shuffle(shuffled_fixture_ids)
    rng.shuffle(shuffled_combinations)

    selected: dict[str, dict[str, str]] = {}
    for fixture_id, (middle_pair, other) in zip(shuffled_fixture_ids, shuffled_combinations):
        selected[fixture_id] = {
            "operation": middle_pair[0],
            "interface": middle_pair[1],
            "cta": other[0],
            "music": other[1],
            "voice": other[2],
        }
    return selected


def deterministic_motion_plan(task_ids: list[str], seed: str) -> dict[str, dict[str, Any]]:
    ranked = sorted(set(task_ids), key=lambda task_id: hashlib.sha256(f"{seed}:{task_id}".encode()).hexdigest())
    dropped = ranked[-1] if len(ranked) % 2 else None
    candidates = ranked[:-1] if dropped else ranked
    selected = set(candidates[: len(candidates) // 2])
    return {
        task_id: {
            "dynamic": task_id in selected,
            "video_model_called": task_id in selected,
            "rank": ranked.index(task_id) + 1,
            "reason": (
                "selected_deterministically_for_background_motion"
                if task_id in selected
                else "odd_batch_candidate_dropped_to_static"
                if task_id == dropped
                else "static_half_not_sent_to_video_model"
            ),
        }
        for task_id in task_ids
    }


def make_layered_master(
    poster_background: Path, poster_foreground: Path, output: Path,
    background_output: Path, foreground_output: Path,
) -> dict[str, Any]:
    background = ImageOps.exif_transpose(Image.open(poster_background)).convert("RGB")
    foreground = ImageOps.exif_transpose(Image.open(poster_foreground)).convert("RGBA")
    if background.size != (2048, 2560) or foreground.size != (2048, 2560):
        raise VideoGenerationError(f"Poster layers must both be 2048x2560: {background.size}, {foreground.size}")
    canvas = ImageOps.fit(background, (1080, 1920), method=Image.Resampling.LANCZOS)
    canvas = canvas.filter(ImageFilter.GaussianBlur(35))
    canvas = Image.blend(canvas, Image.new("RGB", canvas.size, (8, 12, 16)), 0.35).convert("RGBA")
    fitted_background = background.resize((1080, 1350), Image.Resampling.LANCZOS)
    fitted_foreground = foreground.resize((1080, 1350), Image.Resampling.LANCZOS)
    x, y = 0, 285
    canvas.alpha_composite(fitted_background.convert("RGBA"), (x, y))
    locked = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    locked.alpha_composite(fitted_foreground, (x, y))
    master = canvas.copy()
    master.alpha_composite(locked)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(background_output, "PNG", optimize=True)
    locked.save(foreground_output, "PNG", optimize=True)
    master.convert("RGB").save(output, "PNG", optimize=True)
    return {
        "canvas": [1080, 1920],
        "poster_box": [x, y, 1080, 1635],
        "poster_source_size": [2048, 2560],
        "background_master": str(background_output),
        "foreground_master": str(foreground_output),
        "cropped": False,
    }


def make_exact_hook(master: Path, raw_motion: Path, foreground: Path, output: Path, seconds: float = 4.0) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if raw_motion.resolve() == output.resolve():
        raise VideoGenerationError(f"Raw Dreamina motion input and hook output are the same file: {output}")
    filter_graph = (
        "[0:v]scale=1080:1920,trim=duration=0.12,setpts=PTS-STARTPTS,fps=30[still];"
        "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,trim=duration={seconds - 0.12:.2f},"
        "setpts=PTS-STARTPTS,fps=30[motion];"
        f"[2:v]format=rgba,trim=duration={seconds - 0.12:.2f},setpts=PTS-STARTPTS,fps=30[fg];"
        "[motion][fg]overlay=0:0:format=auto[locked];[still][locked]concat=n=2:v=1:a=0[outv]"
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(master),
            "-i", str(raw_motion), "-loop", "1", "-i", str(foreground),
            "-filter_complex", filter_graph, "-map", "[outv]", "-t", str(seconds),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ],
        check=True,
        timeout=300,
    )


def locked_foreground_rms(master: Path, frame: Path, foreground: Path) -> float:
    expected = Image.open(master).convert("RGB")
    actual = Image.open(frame).convert("RGB").resize(expected.size, Image.Resampling.LANCZOS)
    mask = Image.open(foreground).convert("RGBA").getchannel("A")
    difference = ImageChops.difference(expected, actual)
    channels = [ImageStat.Stat(channel, mask=mask).rms[0] for channel in difference.split()]
    return sum(value * value for value in channels) ** 0.5 / len(channels) ** 0.5


def validate_layered_hook(hook: Path, master: Path, foreground: Path) -> dict[str, Any]:
    values = []
    with tempfile.TemporaryDirectory(prefix="jaguartv-hook-qa-") as directory:
        for index, second in enumerate((0.0, 2.0, 3.9)):
            frame = Path(directory) / f"frame-{index}.png"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(second), "-i", str(hook), "-frames:v", "1", str(frame)],
                check=True, timeout=60,
            )
            values.append(locked_foreground_rms(master, frame, foreground))
    return {"foreground_rms": [round(value, 3) for value in values], "foreground_stable": max(values) < 20.0}


def submit_dreamina_hook(master: Path, prompt: str, video_config: dict[str, Any]) -> str:
    command = [
        video_config.get("dreamina_command", "dreamina"), "image2video",
        "--image", str(master), "--prompt", prompt,
        "--duration", str(video_config.get("generation_seconds", 4)),
        "--video_resolution", video_config.get("resolution", "720p"),
        "--model_version", video_config["model_id"], "--poll", "0",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VideoGenerationError(_sanitize(str(error))) from error
    if result.returncode != 0:
        raise VideoGenerationError(_sanitize(result.stderr or result.stdout))
    payload = _extract_json(result.stdout)
    task_id = payload.get("submit_id") or payload.get("task_id") or payload.get("id")
    if not task_id:
        raise VideoGenerationError("Dreamina submission returned no task ID")
    return str(task_id)


def _dreamina_raw_downloads(output_dir: Path, task_id: str, known_files: set[Path]) -> list[Path]:
    candidates = sorted(output_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
    # Never treat locally derived videos as Dreamina raw motion. A previous interrupted
    # run can leave hook-*.mp4/final-*.mp4 in the same directory; returning those causes
    # ffmpeg input/output path collisions or recursive composition.
    raw_like = [path for path in candidates if not path.name.startswith(("hook-", "final-"))]
    task_named = [path for path in raw_like if task_id and task_id in path.name]
    if task_named:
        return task_named
    # Some Dreamina CLI versions may not include the submit id in the downloaded filename.
    # In that case accept only newly-created raw-like files from this polling call, never
    # stale raw mp4s already present before query_result ran.
    return [path for path in raw_like if path not in known_files]


def download_dreamina_result(task_id: str, output_dir: Path, command_name: str = "dreamina", timeout_seconds: int = 600, poll_interval: int = 10) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        known_files = set(output_dir.glob("*.mp4"))
        try:
            result = subprocess.run(
                [command_name, "query_result", "--submit_id", task_id, "--download_dir", str(output_dir)],
                capture_output=True, text=True, check=False, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise VideoGenerationError(_sanitize(str(error))) from error
        if result.returncode != 0:
            raise VideoGenerationError(_sanitize(result.stderr or result.stdout))
        candidates = _dreamina_raw_downloads(output_dir, task_id, known_files)
        if candidates:
            return candidates[0]
        payload = _extract_json(result.stdout)
        if _is_terminal_status(payload) and not _payload_video_url(payload):
            raise VideoGenerationError(f"Dreamina task {task_id} finished without a downloadable MP4 ({_terminal_reason(payload)})")
        time.sleep(poll_interval)
    raise VideoGenerationError(f"Dreamina task {task_id} did not produce an MP4 within {timeout_seconds}s")


def generate_apimart_hook(
    master: Path,
    prompt: str,
    route: dict[str, Any],
    output: Path,
    *,
    timeout_seconds: int = 900,
    poll_interval: int = 5,
) -> dict[str, Any]:
    state_path = output.with_suffix(".apimart-retry.json")
    try:
        return retry_forever(
            lambda: _generate_apimart_hook_once(
                master, prompt, route, output, state_path,
                timeout_seconds=timeout_seconds, poll_interval=poll_interval,
            ),
            state_path=state_path, operation_name=f"video:{output.stem}",
        )
    except Exception as error:
        raise VideoGenerationError(f"APIMart video failed permanently: {_sanitize(str(error))}") from error


def _generate_apimart_hook_once(
    master: Path, prompt: str, route: dict[str, Any], output: Path, state_path: Path,
    *, timeout_seconds: int, poll_interval: int,
) -> dict[str, Any]:
    provider_id = str(route.get("provider_id") or "apimart")
    model_id = str(route.get("model_id") or "wan2.6-i2v-flash")
    base_url = resolve_base_url(route)
    api_key = resolve_secret(route)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        task_id = str(state.get("provider_task_id") or "")
        if not task_id:
            with master.open("rb") as image:
                response = requests.post(
                    f"{base_url}/uploads/images", headers=headers,
                    files={"file": (master.name, image, "image/png")}, timeout=120,
                )
            upload = _response_json(response, "image upload")
            image_url = upload.get("url") or (upload.get("data") or {}).get("url")
            if not image_url:
                raise VideoGenerationError("APIMart image upload returned no URL")
            image_url = requests.utils.requote_uri(str(image_url))

            response = requests.post(
                f"{base_url}/videos/generations",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": model_id, "prompt": prompt, "image_urls": [image_url],
                    "resolution": route.get("resolution", "720p"),
                    "duration": int(route.get("generation_seconds", 4)),
                }, timeout=120,
            )
            submission = _response_json(response, "video submission")
            task_id = _apimart_task_id(submission)
            if not task_id:
                raise VideoGenerationError("APIMart video submission returned no task ID")
            _write_state(state_path, {"provider_task_id": task_id, "provider_task_status": "polling"})

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = requests.get(f"{base_url}/tasks/{task_id}", headers=headers, timeout=60)
            payload = _response_json(response, "task polling")
            task = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            status = str(task.get("status") or "").lower()
            if status == "completed":
                video_url = _apimart_video_url(task)
                if not video_url:
                    raise VideoGenerationError("APIMart task completed without a video URL")
                download = requests.get(video_url, timeout=180)
                if not download.ok or not download.content:
                    raise VideoGenerationError(f"APIMart video download failed with HTTP {download.status_code}")
                if len(download.content) < 20_000:
                    _write_state(state_path, {"provider_task_id": "", "provider_task_status": "invalid_download"})
                    raise VideoGenerationError("APIMart downloaded video is too small; task will be resubmitted")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(download.content)
                return {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "task_id": task_id,
                    "raw_video": str(output),
                    "resolution": route.get("resolution", "720p"),
                    "generation_seconds": int(route.get("generation_seconds", 4)),
                    "status": "ok",
                }
            if status in {"failed", "cancelled", "error"}:
                _write_state(state_path, {"provider_task_id": "", "provider_task_status": status})
                raise VideoGenerationError(f"APIMart task failed: status={status}")
            time.sleep(poll_interval)
        raise VideoGenerationError(f"APIMart task {task_id} did not complete within {timeout_seconds}s")
    except (OSError, requests.RequestException, ValueError, TypeError) as error:
        raise VideoGenerationError(_sanitize(str(error))) from error


def media_duration(path: str | Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return float(result.stdout.strip())


def composition_duration(components: dict[str, str], hook_seconds: float = 4.0, static_cta_seconds: float = 3.0) -> float:
    middle = media_duration(components["operation"]) + media_duration(components["interface"])
    cta_path = Path(components["cta"])
    cta = media_duration(cta_path) if cta_path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} else static_cta_seconds
    return hook_seconds + middle + cta


def compose_v7(
    repository: Path,
    *,
    master: Path,
    hook: Path,
    output: Path,
    components: dict[str, str],
) -> float:
    node = resolve_command("node")
    if not node:
        raise VideoGenerationError("Node.js runtime is unavailable")
    command = [
        node, str(repository / "scripts/compose-video.mjs"),
        "--poster", str(master), "--hook-video", str(hook), "--poster-sec", "4",
        "--modules", f"{components['operation']},{components['interface']}",
        "--cta", components["cta"], "--music", components["music"],
        "--voice", components["voice"], "--output", str(output),
    ]
    result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False, timeout=900)
    if result.returncode != 0:
        raise VideoGenerationError(_sanitize(result.stderr or result.stdout or "compose-video failed"))
    return composition_duration(components)


def write_build_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    try:
        value = json.loads(output)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _response_json(response: requests.Response, operation: str) -> dict[str, Any]:
    if not response.ok:
        raise VideoGenerationError(f"APIMart {operation} failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise VideoGenerationError(f"APIMart {operation} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise VideoGenerationError(f"APIMart {operation} returned an invalid response")
    return payload


def _apimart_task_id(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("task_id") or data[0].get("id") or "")
    if isinstance(data, dict):
        return str(data.get("task_id") or data.get("id") or "")
    return str(payload.get("task_id") or payload.get("id") or "")


def _apimart_video_url(task: dict[str, Any]) -> str:
    videos = (task.get("result") or {}).get("videos") or []
    for video in videos:
        urls = video.get("url") if isinstance(video, dict) else None
        if isinstance(urls, str) and urls.startswith(("http://", "https://")):
            return urls
        if isinstance(urls, list):
            value = next((str(url) for url in urls if str(url).startswith(("http://", "https://"))), "")
            if value:
                return value
    return ""


def _sanitize(message: str) -> str:
    safe = re.sub(r"sk-[A-Za-z0-9_.-]+", "[redacted]", message)
    return " | ".join(line.strip() for line in safe.splitlines() if line.strip())[-1000:]


def _safe(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", "-", value).strip(" .-") or "untitled"
