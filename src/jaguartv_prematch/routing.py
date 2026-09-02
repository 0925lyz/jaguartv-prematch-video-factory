from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .records import ProviderUse


class ProviderRoutingError(RuntimeError):
    pass


class CodexDeepSeekRouter:
    ALLOWED_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}

    def __init__(self, provider_id: str = "deepseek") -> None:
        if provider_id != "deepseek":
            raise ProviderRoutingError("Unsupported text provider")
        self.provider_id = provider_id

    def verify(self, model_id: str, timeout: int = 90) -> ProviderUse:
        self._validate_model(model_id)
        started = _now()
        command = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "-c", 'model_provider="deepseek"', "-m", model_id,
            "Reply exactly: ROUTE-OK",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = f"{result.stdout}\n{result.stderr}"
        status = "ok" if result.returncode == 0 and "ROUTE-OK" in output else "failed"
        return ProviderUse(
            provider_id=self.provider_id,
            model_id=model_id,
            started_at=started,
            completed_at=_now(),
            status=status,
            sanitized_error=None if status == "ok" else _sanitize_error(output),
        )

    def generate(self, model_id: str, prompt: str, output_path: Path, timeout: int = 300) -> ProviderUse:
        self._validate_model(model_id)
        started = _now()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "codex", "exec", "--ephemeral", "--skip-git-repo-check",
                "-c", 'model_provider="deepseek"', "-m", model_id,
                "-o", str(output_path), prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        status = "ok" if result.returncode == 0 and output_path.exists() else "failed"
        return ProviderUse(
            provider_id=self.provider_id,
            model_id=model_id,
            started_at=started,
            completed_at=_now(),
            status=status,
            sanitized_error=None if status == "ok" else _sanitize_error(result.stderr or result.stdout),
        )

    def _validate_model(self, model_id: str) -> None:
        if model_id not in self.ALLOWED_MODELS:
            raise ProviderRoutingError(f"Model {model_id!r} is not valid for provider {self.provider_id!r}")


def append_provider_record(path: Path, record: ProviderUse) -> None:
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    records.append(asdict(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize_error(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    return " | ".join(lines[-3:])[:1000]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
