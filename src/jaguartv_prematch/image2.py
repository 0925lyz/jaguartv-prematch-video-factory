from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .records import ProviderUse
from .credentials import resolve_base_url, resolve_secret


class ImageGenerationError(RuntimeError):
    def __init__(self, message: str, attempts: list[ProviderUse]) -> None:
        super().__init__(message)
        self.attempts = attempts


def generate_image2(
    prompt: str,
    output: Path,
    image_config: dict[str, Any],
    *,
    timeout: int = 300,
) -> list[ProviderUse]:
    attempts: list[ProviderUse] = []
    for index, route_name in enumerate(("primary", "fallback")):
        route = image_config[route_name]
        provider_id = route["provider_id"]
        model_id = image_config["model_id"]
        started = _now()
        try:
            base_url = resolve_base_url(route)
            api_key = resolve_secret(route)
            endpoint = base_url if base_url.endswith("/images/generations") else f"{base_url}/v1/images/generations"
            payload = json.dumps({
                "model": model_id,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1536",
            }).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            image = result["data"][0]
            output.parent.mkdir(parents=True, exist_ok=True)
            if image.get("b64_json"):
                output.write_bytes(base64.b64decode(image["b64_json"]))
            elif image.get("url"):
                with urllib.request.urlopen(image["url"], timeout=timeout) as response:
                    output.write_bytes(response.read())
            else:
                raise RuntimeError("Image provider returned no image data")
            attempts.append(ProviderUse(provider_id, model_id, started, _now(), "ok",
                                        fallback_from=image_config["primary"]["provider_id"] if index else None))
            return attempts
        except (KeyError, OSError, RuntimeError, urllib.error.URLError) as error:
            attempts.append(ProviderUse(provider_id, model_id, started, _now(), "failed",
                                        fallback_from=image_config["primary"]["provider_id"] if index else None,
                                        sanitized_error=_sanitize(error)))
    raise ImageGenerationError("Both configured Image2 routes failed", attempts)


def save_image_route_manifest(path: Path, attempts: list[ProviderUse]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(item) for item in attempts], ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} from configured image provider"
    if isinstance(error, KeyError):
        return f"Required configuration or response field missing: {error.args[0]}"
    return str(error)[:500]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
