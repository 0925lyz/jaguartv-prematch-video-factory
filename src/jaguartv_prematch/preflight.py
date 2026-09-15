from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import FactoryConfig
from .credentials import resolve_base_url, resolve_secret
from .media_inventory import discover_inventory
from .routing import CodexTextRouter
from .runtime import resolve_command


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def run_preflight(config: FactoryConfig, repository: Path) -> dict[str, Any]:
    checks: list[Check] = []
    for command in ("codex", "agent-reach", "ffmpeg", "ffprobe", "node"):
        resolved = resolve_command(command)
        checks.append(Check(command, "ok" if resolved else "failed", resolved or "command not found"))

    text = config.data["text"]
    route = CodexTextRouter(text["provider_id"]).verify(text["primary_model_id"])
    checks.append(Check("text_route", route.status, route.sanitized_error or route.model_id))

    doctor = _run_json(["agent-reach", "doctor", "--json"], timeout=120)
    web_ok = doctor.get("web", {}).get("status") == "ok"
    checks.append(Check("agent_reach_web", "ok" if web_ok else "failed", doctor.get("web", {}).get("message", "unavailable")))
    social_platforms = ("twitter", "reddit", "facebook", "instagram")
    active_social = [name for name in social_platforms if doctor.get(name, {}).get("active_backend")]
    checks.append(Check("agent_reach_social", "ok" if active_social else "blocked",
                        ", ".join(active_social) if active_social else "no social backend was live-verified"))

    image = config.data["image"]
    for route_name in ("primary", "fallback"):
        route_config = image[route_name]
        try:
            model_present = _image_capability(route_config, image["model_id"])
            configured = model_present
        except Exception:
            configured = False
        checks.append(Check(f"image_{route_name}", "ok" if configured else "blocked",
                            route_config["provider_id"]))

    dreamina_command = resolve_command(config.data["video"].get("dreamina_command", "dreamina"))
    dreamina = _run_json([dreamina_command, "user_credit"], timeout=60) if dreamina_command else {}
    tier = str(dreamina.get("vip_level", "")).strip()
    checks.append(Check("dreamina_vip_session", "ok" if tier else "blocked",
                        f"authenticated VIP tier: {tier}" if tier else "VIP session not verified"))
    video_fallback = config.data["video"]["fallback"]
    try:
        fallback_ok = _image_capability(video_fallback, video_fallback["model_id"])
    except Exception:
        fallback_ok = False
    checks.append(Check("video_fallback", "ok" if fallback_ok else "blocked",
                        f"{video_fallback['provider_id']}:{video_fallback['model_id']}"))

    required_assets = [
        repository / "assets/brand/jaguartv-logo.png",
        repository / "scripts/compose-video.mjs",
    ]
    for asset in required_assets:
        checks.append(Check(f"asset:{asset.name}", "ok" if asset.is_file() else "failed", str(asset)))
    try:
        inventory = discover_inventory(repository / "assets")
        for category, paths in inventory.items():
            checks.append(Check(f"inventory:{category}", "ok", f"{len(paths)} local files"))
    except (FileNotFoundError, ValueError) as error:
        checks.append(Check("inventory", "failed", str(error)))

    required_checks = [check for check in checks if check.name not in {"dreamina_vip_session", "video_fallback"}]
    video_ready = tier or fallback_ok
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": [asdict(check) for check in checks],
        "production_ready": bool(video_ready) and all(check.status in {"ok", "configured"} for check in required_checks),
    }
    return report


def _run_json(command: list[str], timeout: int) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def _image_capability(route: dict[str, Any], model_id: str) -> bool:
    base_url = resolve_base_url(route)
    key = resolve_secret(route)
    endpoint = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return model_id in {str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict)}
