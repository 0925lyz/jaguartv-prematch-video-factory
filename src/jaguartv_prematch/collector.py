from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as calendar_date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .records import Fixture


BRASILIA = ZoneInfo("America/Sao_Paulo")


class FixtureCollectionError(RuntimeError):
    pass


def tomorrow_brasilia(now: datetime | None = None) -> str:
    local_now = now.astimezone(BRASILIA) if now else datetime.now(BRASILIA)
    return (local_now.date() + timedelta(days=1)).isoformat()


def resolve_target_date(value: str, now: datetime | None = None) -> str:
    local_now = now.astimezone(BRASILIA) if now else datetime.now(BRASILIA)
    normalized = value.strip().lower()
    if normalized == "today":
        return local_now.date().isoformat()
    if normalized == "tomorrow":
        return (local_now.date() + timedelta(days=1)).isoformat()
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, pattern).date().isoformat()
        except ValueError:
            pass
    raise FixtureCollectionError(f"invalid target date: {value}; expected today, tomorrow, YYYY-MM-DD, or YYYYMMDD")


def collect_fixtures(
    base_url: str,
    *,
    api_key: str = "",
    date: str = "tomorrow",
    timeout_seconds: int = 30,
) -> tuple[list[Fixture], dict[str, Any]]:
    target_date = resolve_target_date(date)
    parsed = urllib.parse.urlsplit(base_url)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    if parsed.path.endswith("/api/save-agenda"):
        query.update({"dedup": "true", "t": str(int(time.time() * 1000))})
    else:
        query["date"] = target_date
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    headers = {"Accept": "application/json", "User-Agent": "JaguarTV-Prematch/0.1"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise FixtureCollectionError(f"collector returned HTTP {error.code}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureCollectionError(f"collector request failed: {type(error).__name__}") from error

    rows = _payload_rows(payload)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    fixtures = [
        _normalize_fixture(row, target_date=target_date, retrieved_at=retrieved_at)
        for row in rows
        if isinstance(row, dict)
    ]
    return [fixture for fixture in fixtures if fixture.schedule_date == target_date], payload


def load_fixture_file(path: Path) -> tuple[list[Fixture], dict[str, Any]]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    rows = _payload_rows(payload)
    if not isinstance(rows, list):
        raise FixtureCollectionError("manual fixture file must contain a fixtures array")
    fixtures = [_normalize_fixture(row) for row in rows if isinstance(row, dict)]
    return fixtures, {"manual_fixture_file": str(path), "fixtures": [fixture.to_dict() for fixture in fixtures]}


def _payload_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get("fixtures") or payload.get("matches") or []
        return rows if isinstance(rows, list) else []
    return []


def _normalize_fixture(
    row: dict[str, Any],
    *,
    target_date: str | None = None,
    retrieved_at: str = "",
) -> Fixture:
    teams = row.get("teams", {}) if isinstance(row.get("teams"), dict) else {}
    league = row.get("league", {}) if isinstance(row.get("league"), dict) else {}
    home = teams.get("home", {}) if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away", {}) if isinstance(teams.get("away"), dict) else {}
    channels = row.get("channels") or ([row["channel"]] if row.get("channel") else [])
    channel_names = tuple(
        str(item.get("name", "")).strip() if isinstance(item, dict) else str(item).strip()
        for item in channels
        if item
    )
    schedule_date = _schedule_date(str(row.get("schedule_date") or row.get("date") or ""), target_date)
    return Fixture(
        fixture_id=str(row.get("fixture_id") or row.get("id") or row.get("fingerprint") or ""),
        competition=str(row.get("competition") or league.get("name") or row.get("league") or "").strip(),
        home_team=str(row.get("home_team") or row.get("homeTeam") or home.get("name") or teams.get("home") or "").strip(),
        away_team=str(row.get("away_team") or row.get("awayTeam") or away.get("name") or teams.get("away") or "").strip(),
        schedule_date=schedule_date,
        kickoff_at_brt=str(row.get("kickoff_at_brt") or row.get("kickoff_time") or row.get("time") or ""),
        channels=channel_names,
        featured=bool(row.get("featured") or row.get("is_featured") or row.get("isHot") or (row.get("isVisible", True) and channel_names)),
        source_url=str(row.get("source_url") or row.get("url") or "https://copa.jarg.top/jogos-de-hoje"),
        retrieved_at=str(row.get("retrieved_at") or row.get("retrieval_timestamp") or retrieved_at),
        source_text=str(row.get("source_text") or row.get("original_source_text") or json.dumps(row, ensure_ascii=False, sort_keys=True)),
        league_id=_optional_int(row.get("league_id") or league.get("id")),
        league_country=str(row.get("league_country") or league.get("country") or "").strip(),
        league_season=_optional_int(row.get("league_season") or league.get("season")),
        home_team_id=_optional_int(row.get("home_team_id") or home.get("id")),
        away_team_id=_optional_int(row.get("away_team_id") or away.get("id")),
        home_crest_url=str(row.get("home_crest_url") or row.get("home_logo") or row.get("homeLogo") or home.get("logo") or "").strip(),
        away_crest_url=str(row.get("away_crest_url") or row.get("away_logo") or row.get("awayLogo") or away.get("logo") or "").strip(),
        verified_brazilian_players=tuple(row.get("verified_brazilian_players", [])),
        raw=row,
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _schedule_date(value: str, target_date: str | None) -> str:
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    if target_date:
        target = calendar_date.fromisoformat(target_date)
        if value == target.strftime("%d/%m"):
            return target.isoformat()
    return value


def save_collection(fixtures: list[Fixture], raw: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "timezone_label": "Horário de Brasília",
        "fixtures": [fixture.to_dict() for fixture in fixtures],
        "source_payload": raw,
    }
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
