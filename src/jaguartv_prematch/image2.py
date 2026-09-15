from __future__ import annotations

import base64
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .records import ProviderUse
from .credentials import resolve_base_url, resolve_secret
from .retry import retry_forever, _write_state


class ImageGenerationError(RuntimeError):
    def __init__(self, message: str, attempts: list[ProviderUse]) -> None:
        super().__init__(message)
        self.attempts = attempts


def generate_image2(
    prompt: str,
    output: Path,
    image_config: dict[str, Any],
    *,
    timeout: int = 120,
    max_retries: int = 4,
    retry_backoff: float = 8.0,
) -> list[ProviderUse]:
    attempts: list[ProviderUse] = []
    model_id = image_config["model_id"]
    state_path = output.with_suffix(".apimart-retry.json")

    def call(route: dict[str, Any], fallback_from: str | None = None) -> None:
        provider_id = route["provider_id"]
        started = _now()
        try:
            base_url = resolve_base_url(route)
            api_key = resolve_secret(route)
            image: dict[str, Any] | None = None
            if provider_id == "apimart" and state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                task_id = str(state.get("provider_task_id") or "")
                if task_id:
                    image = _poll_apimart(base_url, api_key, task_id, timeout, state_path=state_path)
            if image is None:
                endpoint = _image_endpoint(base_url)
                payload = json.dumps({
                    "model": model_id, "prompt": prompt, "n": 1,
                    "size": image_config.get("size", "1024x1280"),
                }).encode("utf-8")
                request = urllib.request.Request(
                    endpoint, data=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    result = json.load(response)
                image = _resolve_image(result, base_url, api_key, timeout, state_path if provider_id == "apimart" else None)
            output.parent.mkdir(parents=True, exist_ok=True)
            if image.get("b64_json"):
                output.write_bytes(base64.b64decode(image["b64_json"]))
            elif image.get("url"):
                output.write_bytes(_download_image_url(image["url"], timeout))
            else:
                raise RuntimeError("Image provider returned no image data")
            _verify_requested_size(output, image_config.get("size", "1024x1280"))
        except (KeyError, OSError, RuntimeError, urllib.error.URLError, http.client.HTTPException) as error:
            attempts.append(ProviderUse(provider_id, model_id, started, _now(), "failed", fallback_from=fallback_from, sanitized_error=_sanitize(error)))
            raise
        attempts.append(ProviderUse(provider_id, model_id, started, _now(), "ok", fallback_from=fallback_from))

    primary = image_config["primary"]
    primary_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            call(primary)
            return attempts
        except Exception as error:
            primary_error = error
            if attempt < max_retries - 1:
                time.sleep(retry_backoff * (attempt + 1))

    fallback = image_config["fallback"]
    if fallback.get("provider_id") != "apimart":
        raise ImageGenerationError(f"Primary Image2 failed and configured fallback is not APIMart: {_sanitize(primary_error or RuntimeError('unknown'))}", attempts)
    try:
        retry_forever(
            lambda: call(fallback, str(primary.get("provider_id"))),
            state_path=state_path, operation_name=f"image2:{output.stem}",
        )
    except Exception as error:
        raise ImageGenerationError(f"APIMart Image2 fallback failed permanently: {_sanitize(error)}", attempts) from error
    return attempts


def _image_endpoint(base_url: str) -> str:
    if base_url.endswith("/images/generations"):
        return base_url
    return f"{base_url}/images/generations" if base_url.endswith("/v1") else f"{base_url}/v1/images/generations"


def save_image_route_manifest(path: Path, attempts: list[ProviderUse]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(item) for item in attempts], ensure_ascii=False, indent=2), encoding="utf-8")


def _verify_requested_size(output: Path, requested: str, tolerance: float = 0.02) -> None:
    """Reject a poster whose aspect ratio does not match the requested size.

    Image2 providers sometimes ignore ``size`` and return an arbitrary canvas — one
    fallback run returned 793x1983 (2:5) for a 4:5 request, which then shipped as an
    off-spec poster. Raising here marks the attempt as failed so the retry loop can
    re-request it instead of silently saving the wrong aspect ratio.
    """
    match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(requested))
    if not match:
        return
    want_w, want_h = int(match.group(1)), int(match.group(2))
    if want_w <= 0 or want_h <= 0:
        return
    from PIL import Image

    with Image.open(output) as image:
        got_w, got_h = image.size
    if got_w <= 0 or got_h <= 0:
        raise RuntimeError(f"image provider returned an empty canvas ({got_w}x{got_h})")
    want_ratio = want_w / want_h
    got_ratio = got_w / got_h
    if abs(got_ratio - want_ratio) / want_ratio > tolerance:
        raise RuntimeError(
            f"image provider returned {got_w}x{got_h} for the requested {want_w}x{want_h} canvas"
        )


def _sanitize(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} from configured image provider"
    if isinstance(error, KeyError):
        return f"Required configuration or response field missing: {error.args[0]}"
    return str(error)[:500]


def _download_image_url(url: str, timeout: int, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                data = response.read()
            if data:
                return data
            raise RuntimeError("Image provider returned an empty image")
        except (http.client.IncompleteRead, TimeoutError, urllib.error.URLError, OSError) as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"image URL download failed after {attempts} attempts: {_sanitize(last_error or RuntimeError('unknown'))}")


def _resolve_image(result: Any, base_url: str, api_key: str, timeout: int, state_path: Path | None = None) -> dict[str, Any]:
    """Normalize a provider response into an OpenAI-style image descriptor.

    Handles both synchronous OpenAI-compatible responses (data[0].b64_json/url)
    and async task-based providers such as apimart (data[0].task_id -> poll).
    """
    items = result.get("data") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError("Image provider returned no usable data")
    first = items[0]
    if first.get("b64_json") or first.get("url"):
        return first
    task_id = first.get("task_id")
    if task_id:
        if state_path:
            _write_state(state_path, {"provider_task_id": str(task_id), "provider_task_status": "polling"})
        return _poll_apimart(base_url, api_key, task_id, timeout, state_path=state_path)
    raise RuntimeError("Image provider returned no image data")


def _poll_apimart(base_url: str, api_key: str, task_id: str, timeout: int, max_poll: int = 40, interval: float = 5.0, state_path: Path | None = None) -> dict[str, Any]:
    """Poll an apimart async image task until completion and return {url: ...}."""
    poll_url = f"{base_url}/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}"
    last_status = None
    for _ in range(max_poll):
        request = urllib.request.Request(
            poll_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        last_status = data.get("status")
        if data.get("progress", 0) >= 100 or last_status == "completed":
            images = (data.get("result") or {}).get("images") or []
            if images and images[0].get("url"):
                url = images[0]["url"]
                if isinstance(url, list):
                    url = url[0]
                return {"url": url}
        if str(last_status).lower() in {"failed", "error", "cancelled", "canceled"}:
            if state_path:
                _write_state(state_path, {"provider_task_id": "", "provider_task_status": str(last_status)})
            raise RuntimeError(f"apimart task failed: {task_id} status={last_status}")
        time.sleep(interval)
    raise RuntimeError(f"apimart task {task_id} polling timed out (last status: {last_status})")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
