from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from jaguartv_prematch.media_inventory import (
    commit_rotation,
    discard_stale_pending,
    discover_inventory,
    reserve_rotation,
)
from jaguartv_prematch.video import media_duration, validate_av_duration


def _inventory(tmp_path: Path, *, operations: int = 3) -> tuple[Path, dict[str, list[Path]]]:
    root = tmp_path / "assets"
    names = {
        "operation": [f"op-{index}.mp4" for index in range(operations)],
        "cta": ["cta-1.mp4", "cta-2.mp4"],
        "music": ["music-1.mp3", "music-2.m4a"],
        "voice": ["voice-1.wav", "voice-2.wav"],
    }
    folders = {
        "operation": root / "video" / "operation",
        "cta": root / "video" / "cta",
        "music": root / "audio" / "music",
        "voice": root / "audio" / "voiceover",
    }
    for category, filenames in names.items():
        folders[category].mkdir(parents=True, exist_ok=True)
        for filename in reversed(filenames):
            (folders[category] / filename).write_bytes(filename.encode())
    (folders["cta"] / "old-static.jpg").write_bytes(b"ignore")
    (folders["voice"] / "voice-1.transcript.json").write_text("{}")
    return root, discover_inventory(root)


def test_inventory_classification_and_stable_order(tmp_path: Path) -> None:
    _root, inventory = _inventory(tmp_path)
    assert [path.name for path in inventory["operation"]] == ["op-0.mp4", "op-1.mp4", "op-2.mp4"]
    assert [path.name for path in inventory["cta"]] == ["cta-1.mp4", "cta-2.mp4"]
    assert [path.name for path in inventory["music"]] == ["music-1.mp3", "music-2.m4a"]
    assert [path.name for path in inventory["voice"]] == ["voice-1.wav", "voice-2.wav"]


def test_rotation_persists_wraps_and_advances_only_on_commit(tmp_path: Path) -> None:
    _root, inventory = _inventory(tmp_path)
    state_path = tmp_path / "runtime" / "media-rotation.json"

    first = reserve_rotation(state_path, inventory, "job-1")
    assert [Path(path).name for path in first["components"]["operation"]] == ["op-0.mp4", "op-1.mp4"]
    assert Path(first["components"]["cta"]).name == "cta-1.mp4"
    state = json.loads(state_path.read_text())
    assert state["categories"] == {}

    assert reserve_rotation(state_path, inventory, "job-1")["components"] == first["components"]
    commit_rotation(state_path, "job-1")

    second = reserve_rotation(state_path, discover_inventory(_root), "job-2")
    assert [Path(path).name for path in second["components"]["operation"]] == ["op-2.mp4", "op-0.mp4"]
    assert Path(second["components"]["cta"]).name == "cta-2.mp4"
    assert Path(second["components"]["music"]).name == "music-2.m4a"
    assert Path(second["components"]["voice"]).name == "voice-2.wav"


def test_failed_reservation_does_not_consume_or_skip_new_assets(tmp_path: Path) -> None:
    root, inventory = _inventory(tmp_path, operations=2)
    state_path = tmp_path / "runtime" / "media-rotation.json"
    failed = reserve_rotation(state_path, inventory, "failed-job")
    discard_stale_pending(state_path, set())
    retried = reserve_rotation(state_path, discover_inventory(root), "next-job")
    assert retried["components"] == failed["components"]

    commit_rotation(state_path, "next-job")
    new_asset = root / "video" / "operation" / "00-new.mp4"
    new_asset.write_bytes(b"new")
    following = reserve_rotation(state_path, discover_inventory(root), "following-job", operation_count=1)
    assert Path(following["components"]["operation"][0]).name == "00-new.mp4"


def test_multiple_pending_jobs_keep_sequential_reservations(tmp_path: Path) -> None:
    _root, inventory = _inventory(tmp_path)
    state_path = tmp_path / "runtime" / "media-rotation.json"
    first = reserve_rotation(state_path, inventory, "job-1")
    second = reserve_rotation(state_path, inventory, "job-2")
    assert [Path(path).name for path in first["components"]["operation"]] == ["op-0.mp4", "op-1.mp4"]
    assert [Path(path).name for path in second["components"]["operation"]] == ["op-2.mp4", "op-0.mp4"]
    commit_rotation(state_path, "job-1")
    commit_rotation(state_path, "job-2")


def test_stale_failed_job_drops_dependent_pending_reservations(tmp_path: Path) -> None:
    _root, inventory = _inventory(tmp_path)
    state_path = tmp_path / "runtime" / "media-rotation.json"
    first = reserve_rotation(state_path, inventory, "failed-job")
    reserve_rotation(state_path, inventory, "queued-job")
    discard_stale_pending(state_path, {"queued-job"})
    retried = reserve_rotation(state_path, inventory, "queued-job")
    assert retried["components"] == first["components"]


def test_short_music_loops_to_exact_video_duration(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe") or not shutil.which("node"):
        pytest.skip("FFmpeg, ffprobe, and Node.js are required")

    def video(name: str, seconds: float, color: str) -> Path:
        path = tmp_path / name
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=108x192:r=30:d={seconds}", "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(path),
        ], check=True)
        return path

    def audio(name: str, seconds: float, frequency: int) -> Path:
        path = tmp_path / name
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"sine=frequency={frequency}:duration={seconds}", "-c:a", "pcm_s16le", str(path),
        ], check=True)
        return path

    hook = video("hook.mp4", 0.2, "red")
    operation = video("operation.mp4", 0.3, "green")
    interface = video("interface.mp4", 0.4, "blue")
    cta = video("cta.mp4", 0.5, "yellow")
    music = audio("music.wav", 0.1, 440)
    voice = audio("voice.wav", 0.8, 880)
    poster = tmp_path / "poster.png"
    Image.new("RGB", (108, 192), "black").save(poster)
    output = tmp_path / "final.mp4"
    repository = Path(__file__).resolve().parents[1]
    subprocess.run([
        "node", str(repository / "scripts" / "compose-video.mjs"),
        "--poster", str(poster), "--hook-video", str(hook), "--poster-sec", "0.2",
        "--modules", f"{operation},{interface}", "--cta", str(cta), "--music", str(music),
        "--voice", str(voice), "--output", str(output),
    ], cwd=repository, check=True)
    expected = 0.2 + media_duration(operation) + media_duration(interface) + media_duration(cta)
    durations = validate_av_duration(output, expected, tolerance=0.15)
    assert abs(durations["audio"] - durations["video"]) <= 0.15
