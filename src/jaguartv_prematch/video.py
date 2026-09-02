from __future__ import annotations

import hashlib
import itertools
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from .runtime import resolve_command


class VideoGenerationError(RuntimeError):
    pass


def video_filenames(poster_path: str | Path, source_seconds: int = 4) -> dict[str, str]:
    stem = _safe(Path(poster_path).stem)
    return {
        "media_stem": stem,
        "master": f"master-{stem}-1080x1920.png",
        "raw_video": f"jimeng-{stem}-{source_seconds}s.mp4",
        "hook": f"hook-{stem}-3s.mp4",
        "final": f"final-{stem}-12s.mp4",
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
    if len(fixture_ids) > len(middle_pairs) * len(other_combinations):
        raise VideoGenerationError("Component pools cannot provide unique same-day combinations")
    offset = int(hashlib.sha256(date_brt.encode()).hexdigest(), 16)
    return {
        fixture_id: {
            "operation": middle_pairs[(offset + index) % len(middle_pairs)][0],
            "interface": middle_pairs[(offset + index) % len(middle_pairs)][1],
            "cta": other_combinations[((offset // 7) + index) % len(other_combinations)][0],
            "music": other_combinations[((offset // 7) + index) % len(other_combinations)][1],
            "voice": other_combinations[((offset // 7) + index) % len(other_combinations)][2],
        }
        for index, fixture_id in enumerate(fixture_ids)
    }


def make_vertical_master(poster: Path, output: Path) -> dict[str, Any]:
    with Image.open(poster) as source:
        foreground = ImageOps.exif_transpose(source).convert("RGB")
    ratio = foreground.width / foreground.height
    if abs(ratio - 0.8) > 0.02:
        raise VideoGenerationError(f"Poster is not 4:5: {foreground.size}")
    background = ImageOps.fit(foreground, (1080, 1920), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(35))
    background = Image.blend(background, Image.new("RGB", background.size, (8, 12, 16)), 0.35)
    fitted = ImageOps.contain(foreground, (1080, 1350), method=Image.Resampling.LANCZOS)
    x = (1080 - fitted.width) // 2
    y = (1920 - fitted.height) // 2
    background.paste(fitted, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    background.save(output, "PNG")
    return {"canvas": [1080, 1920], "poster_box": [x, y, x + fitted.width, y + fitted.height], "cropped": False}


def make_exact_hook(master: Path, raw_motion: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    logo = Path(__file__).resolve().parents[2] / "assets/brand/jaguartv-logo.png"
    filter_graph = (
        "[0:v]scale=1080:1920,trim=duration=0.12,setpts=PTS-STARTPTS,fps=30[still];"
        "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,trim=duration=2.88,"
        "setpts=PTS-STARTPTS,fps=30[motion];[still][motion]concat=n=2:v=1:a=0[base];"
        "[2:v]scale=184:-1,format=rgba[logo];[base][logo]overlay=W-w-28:28[outv]"
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(master),
            "-i", str(raw_motion), "-i", str(logo), "-filter_complex", filter_graph, "-map", "[outv]", "-t", "3",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ],
        check=True,
        timeout=300,
    )


def submit_dreamina_hook(master: Path, prompt: str, video_config: dict[str, Any]) -> str:
    command = [
        video_config.get("dreamina_command", "dreamina"), "image2video",
        "--image", str(master), "--prompt", prompt,
        "--duration", str(video_config.get("generation_seconds", 4)),
        "--video_resolution", video_config.get("resolution", "720p"),
        "--model_version", video_config["model_id"], "--poll", "0",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
    if result.returncode != 0:
        raise VideoGenerationError(_sanitize(result.stderr or result.stdout))
    payload = _extract_json(result.stdout)
    task_id = payload.get("submit_id") or payload.get("task_id") or payload.get("id")
    if not task_id:
        raise VideoGenerationError("Dreamina submission returned no task ID")
    return str(task_id)


def download_dreamina_result(task_id: str, output_dir: Path, command_name: str = "dreamina") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [command_name, "query_result", "--submit_id", task_id, "--download_dir", str(output_dir)],
        capture_output=True, text=True, check=False, timeout=900,
    )
    if result.returncode != 0:
        raise VideoGenerationError(_sanitize(result.stderr or result.stdout))
    candidates = sorted(output_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise VideoGenerationError("Dreamina result completed without a downloaded MP4")
    return candidates[0]


def compose_v7(
    repository: Path,
    *,
    master: Path,
    hook: Path,
    output: Path,
    components: dict[str, str],
) -> None:
    node = resolve_command("node")
    if not node:
        raise VideoGenerationError("Node.js runtime is unavailable")
    command = [
        node, str(repository / "scripts/compose-video.mjs"),
        "--poster", str(master), "--hook-video", str(hook), "--poster-sec", "3",
        "--modules", f"{components['operation']},{components['interface']}",
        "--cta", components["cta"], "--music", components["music"],
        "--voice", components["voice"], "--output", str(output),
    ]
    result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False, timeout=900)
    if result.returncode != 0:
        raise VideoGenerationError(_sanitize(result.stderr or result.stdout or "compose-video failed"))


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


def _sanitize(message: str) -> str:
    return " | ".join(line.strip() for line in message.splitlines() if line.strip())[-1000:]


def _safe(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", "-", value).strip(" .-") or "untitled"
