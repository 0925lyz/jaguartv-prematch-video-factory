from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class UploadError(RuntimeError):
    pass


def upload_pending_review(
    video_path: Path,
    metadata: dict[str, Any],
    *,
    base_url: str,
    upload_token: str,
    dashboard_token: str,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    video = video_path.expanduser().resolve()
    if not video.is_file() or video.suffix.lower() != ".mp4":
        raise ValueError("Final video must be an existing MP4")
    if metadata.get("category") != "pre_match_prediction":
        raise ValueError("Upload category must be pre_match_prediction")
    match_info = metadata.get("match_info", {})
    if match_info.get("content_category") != "赛前预测":
        raise ValueError("Upload inventory label must be exactly 赛前预测")
    if not upload_token.strip() or not dashboard_token.strip():
        raise ValueError("Upload and dashboard tokens are required")

    url = _validated_base_url(base_url)
    encoded_metadata = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_id = _request_id(video, encoded_metadata)
    headers = {
        "Content-Type": "video/mp4",
        "X-Upload-Token": upload_token,
        "X-Original-Metadata": base64.urlsafe_b64encode(encoded_metadata).decode("ascii").rstrip("="),
        "X-Request-ID": request_id,
        "X-Operator": "jaguartv-prematch-worker",
        "X-Original-Batch-Size": "1",
    }
    try:
        with video.open("rb") as source:
            response = requests.post(
                f"{url}/api/originals/import",
                params={"filename": video.name},
                data=source,
                headers=headers,
                timeout=timeout_seconds,
            )
        response.raise_for_status()
        uploaded = response.json()
    except requests.RequestException as error:
        raise UploadError(_sanitized_request_error(error, "original video upload failed")) from error

    upload_id = str(uploaded.get("id", ""))
    if not upload_id:
        raise UploadError("Upload server returned no record ID")
    verified = _verify_once(url, dashboard_token, upload_id)
    return {
        "upload_id": upload_id,
        "status": verified.get("status_id") or verified.get("status") or "PENDING_REVIEW",
        "inventory_label": "赛前预测",
        "verified_exactly_once": True,
        "request_id": request_id,
    }


def _verify_once(base_url: str, dashboard_token: str, upload_id: str) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{base_url}/api/originals",
            params={"status": "PENDING_REVIEW", "category": "pre_match_prediction"},
            headers={"Authorization": f"Bearer {dashboard_token}"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise UploadError(_sanitized_request_error(error, "Pending Review verification failed")) from error
    items = payload.get("items", payload if isinstance(payload, list) else [])
    matches = [item for item in items if str(item.get("id")) == upload_id]
    if len(matches) != 1:
        raise UploadError(f"Expected exactly one Pending Review record, found {len(matches)}")
    # The list projection hides status_id/category_id (returns null); fetch the
    # authoritative detail record to verify status/category.
    try:
        detail_resp = requests.get(
            f"{base_url}/api/originals/{upload_id}",
            headers={"Authorization": f"Bearer {dashboard_token}"},
            timeout=60,
        )
        detail_resp.raise_for_status()
        item = detail_resp.json()
    except requests.RequestException as error:
        raise UploadError(_sanitized_request_error(error, "Pending Review detail fetch failed")) from error
    status = item.get("status_id") or item.get("status")
    category = item.get("category_id") or item.get("category")
    if status != "PENDING_REVIEW" or category != "pre_match_prediction":
        raise UploadError("Uploaded record is not in Pending Review under 赛前预测")
    return item


def _request_id(video: Path, metadata: bytes) -> str:
    digest = hashlib.sha256(metadata)
    with video.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return f"prematch-{digest.hexdigest()[:32]}"


def _validated_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Dashboard URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Dashboard URL must not contain credentials, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _sanitized_request_error(error: requests.RequestException, fallback: str) -> str:
    response = getattr(error, "response", None)
    if response is not None:
        try:
            detail = str(response.json().get("error", ""))[:300]
            if detail:
                return detail
        except (ValueError, AttributeError):
            pass
        return f"Server returned HTTP {response.status_code}"
    return fallback
