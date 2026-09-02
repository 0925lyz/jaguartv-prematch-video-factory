from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .records import Fixture


BRASILIA = ZoneInfo("America/Sao_Paulo")


def tomorrow_brasilia(now: datetime | None = None) -> str:
    local_now = now.astimezone(BRASILIA) if now else datetime.now(BRASILIA)
    return (local_now.date() + timedelta(days=1)).isoformat()


def collect_fixtures(
    base_url: str,
    *,
    api_key: str = "",
    date: str = "tomorrow",
    timeout_seconds: int = 30,
) -> tuple[list[Fixture], dict[str, Any]]:
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}{urllib.parse.urlencode({'date': date})}"
    headers = {"Accept": "application/json", "User-Agent": "JaguarTV-Prematch/0.1"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.load(response)

    rows = payload.get("fixtures", payload if isinstance(payload, list) else [])
    fixtures = [_normalize_fixture(row) for row in rows]
    return fixtures, payload


def _normalize_fixture(row: dict[str, Any]) -> Fixture:
    teams = row.get("teams", {})
    channels = row.get("channels", [])
    channel_names = tuple(
        str(item.get("name", "")).strip() if isinstance(item, dict) else str(item).strip()
        for item in channels
        if item
    )
    return Fixture(
        fixture_id=str(row.get("fixture_id") or row.get("id") or row.get("fingerprint") or ""),
        competition=str(row.get("competition") or row.get("league") or "").strip(),
        home_team=str(row.get("home_team") or teams.get("home") or "").strip(),
        away_team=str(row.get("away_team") or teams.get("away") or "").strip(),
        schedule_date=str(row.get("schedule_date") or row.get("date") or ""),
        kickoff_at_brt=str(row.get("kickoff_at_brt") or row.get("kickoff_time") or ""),
        channels=channel_names,
        featured=bool(row.get("featured") or row.get("is_featured")),
        source_url=str(row.get("source_url") or row.get("url") or ""),
        retrieved_at=str(row.get("retrieved_at") or row.get("retrieval_timestamp") or ""),
        source_text=str(row.get("source_text") or row.get("original_source_text") or ""),
        verified_brazilian_players=tuple(row.get("verified_brazilian_players", [])),
        raw=row,
    )


def save_collection(fixtures: list[Fixture], raw: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "timezone_label": "Horário de Brasília",
        "fixtures": [fixture.to_dict() for fixture in fixtures],
        "source_payload": raw,
    }
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
