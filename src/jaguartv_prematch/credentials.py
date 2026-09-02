from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


class CredentialUnavailable(RuntimeError):
    pass


def resolve_secret(route: dict[str, Any]) -> str:
    env_name = route.get("api_key_env")
    if env_name and os.environ.get(env_name, "").strip():
        return os.environ[env_name].strip()

    credential_json = route.get("credential_json")
    if credential_json:
        path_value = credential_json.get("path", "~/.codex/auth.json")
        path = Path(path_value).expanduser()
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = str(payload.get(credential_json.get("field", "OPENAI_API_KEY"), "")).strip()
            if value:
                return value

    service = route.get("keychain_service")
    if service:
        account = os.environ.get("USER") or os.environ.get("LOGNAME") or "jaguar"
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", account, "-s", service, "-w"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise CredentialUnavailable(f"Credential is unavailable for provider {route.get('provider_id', 'unknown')}")


def resolve_base_url(route: dict[str, Any]) -> str:
    env_name = route.get("base_url_env")
    value = os.environ.get(env_name, "").strip() if env_name else ""
    value = value or str(route.get("base_url", "")).strip()
    if not value:
        raise CredentialUnavailable(f"Base URL is unavailable for provider {route.get('provider_id', 'unknown')}")
    return value.rstrip("/")
