from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
EXCLUDED_IMAGE_PARTS = {
    "cover", "master-", "background", "reference", "raw", "preview", "discard",
    "rejected", "qa", "contact", "hook", "cta", "resultados", "postmatch", "赛后",
}
EXCLUDED_VIDEO_PARTS = {
    "hook-", "cta", "operation", "downloader", "google-search", "epg", "raw", "visual-clean",
    "discard", "rejected",
}


@dataclass(frozen=True)
class DeliveredArtifact:
    category: str
    source: str
    destination: str
    sha256: str
    operation: str
    duration_seconds: float | None = None


def build_delivery(
    destination: Path,
    *,
    poster_roots: list[Path],
    video_roots: list[Path],
    cta_roots: list[Path],
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, object]:
    posters_dir = destination / "赛前海报"
    operation_dir = destination / "操作类"
    cta_dir = destination / "cta"
    for directory in (posters_dir, operation_dir, cta_dir):
        directory.mkdir(parents=True, exist_ok=True)

    artifacts: list[DeliveredArtifact] = []
    poster_seen: set[str] = set()
    for source in discover_posters(poster_roots):
        digest = sha256(source)
        if digest in poster_seen:
            continue
        poster_seen.add(digest)
        target = collision_safe_target(posters_dir, source.name, digest)
        shutil.copy2(source, target)
        artifacts.append(DeliveredArtifact("赛前海报", str(source), str(target), digest, "copy"))

    cta_seen: set[str] = set()
    for source in discover_cta(cta_roots):
        digest = sha256(source)
        if digest in cta_seen:
            continue
        cta_seen.add(digest)
        target = collision_safe_target(cta_dir, source.name, digest)
        shutil.copy2(source, target)
        artifacts.append(DeliveredArtifact("cta", str(source), str(target), digest, "copy"))

    video_seen: set[str] = set()
    operation_output_seen: set[str] = set()
    for source in discover_finished_videos(video_roots):
        digest = sha256(source)
        if digest in video_seen:
            continue
        video_seen.add(digest)
        duration = probe_duration(source, ffprobe)
        if duration >= 9.0:
            operation_name = f"{source.stem}_操作类_4-9秒_{digest[:10]}.mp4"
            operation_target = collision_safe_target(operation_dir, operation_name, digest)
            extract_segment(source, operation_target, 4.0, 5.0, ffmpeg)
            operation_digest = sha256(operation_target)
            if operation_digest in operation_output_seen:
                operation_target.unlink()
            else:
                operation_output_seen.add(operation_digest)
                artifacts.append(
                    DeliveredArtifact("操作类", str(source), str(operation_target), operation_digest,
                                      "extract_4_to_9_seconds", 5.0)
                )
        if duration >= 3.0:
            cta_name = f"{source.stem}_cta_末3秒_{digest[:10]}.mp4"
            cta_target = collision_safe_target(cta_dir, cta_name, digest)
            extract_segment(source, cta_target, max(0.0, duration - 3.0), 3.0, ffmpeg)
            cta_digest = sha256(cta_target)
            if cta_digest in cta_seen:
                cta_target.unlink()
            else:
                cta_seen.add(cta_digest)
                artifacts.append(
                    DeliveredArtifact("cta", str(source), str(cta_target), cta_digest,
                                      "extract_final_3_seconds", 3.0)
                )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rules": {
            "poster_scope": "completed saved pre-match posters; QA, cover, master, raw and post-match images excluded",
            "operation_segment": "video seconds 4.000 through 9.000",
            "cta_segment": "final 3.000 seconds of every unique completed poster video",
            "deduplication": "SHA-256",
        },
        "counts": {
            "赛前海报": sum(item.category == "赛前海报" for item in artifacts),
            "操作类": sum(item.category == "操作类" for item in artifacts),
            "cta": sum(item.category == "cta" for item in artifacts),
        },
        "artifacts": [asdict(item) for item in artifacts],
    }
    (destination / "交付清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def discover_posters(roots: list[Path]) -> list[Path]:
    results: list[Path] = []
    for root in roots:
        collect_all_images = root.name == "海报"
        for path in _iter_files([root], IMAGE_EXTENSIONS):
            lowered = str(path).lower()
            name = path.name.lower()
            if any(token in lowered for token in EXCLUDED_IMAGE_PARTS):
                continue
            if re.search(r"\d+\s*[：:]\s*\d+", path.name):
                continue
            if collect_all_images or "海报" in path.name or "poster" in name or "schedule" in name:
                results.append(path)
    return sorted(results, key=lambda item: str(item))


def discover_finished_videos(roots: list[Path]) -> list[Path]:
    results: list[Path] = []
    for path in _iter_files(roots, VIDEO_EXTENSIONS):
        lowered = str(path).lower()
        name = path.name.lower()
        if any(token in lowered for token in EXCLUDED_VIDEO_PARTS):
            continue
        if name.startswith("final-") or "成片" in lowered or "jaguartv" in name:
            results.append(path)
    return sorted(results, key=lambda item: str(item))


def discover_cta(roots: list[Path]) -> list[Path]:
    return sorted(_iter_files(roots, VIDEO_EXTENSIONS), key=lambda item: str(item))


def _iter_files(roots: list[Path], extensions: set[str]):
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix.lower() in extensions:
            yield root.resolve()
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in extensions:
                yield path.resolve()


def probe_duration(path: Path, ffprobe: str) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def extract_segment(source: Path, target: Path, start: float, duration: float, ffmpeg: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
        "-i", str(source), "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target),
    ]
    subprocess.run(command, check=True)


def collision_safe_target(directory: Path, filename: str, source_hash: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    if sha256(target) == source_hash:
        return target
    return directory / f"{target.stem}_{source_hash[:10]}{target.suffix.lower()}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
