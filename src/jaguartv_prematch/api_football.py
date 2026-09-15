from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .competition import BRASILEIRAO_SERIE_A_ID, BrasileiraoMembership


def load_brasileirao_membership(
    *, season: int, cache_path: Path, api_key: str = "",
    endpoint: str = "https://v3.football.api-sports.io/teams", timeout: int = 30,
) -> BrasileiraoMembership:
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if int(cached.get("season", 0)) == season and cached.get("team_names"):
            return BrasileiraoMembership(
                season,
                frozenset(int(value) for value in cached.get("team_ids") or []),
                frozenset(str(value) for value in cached.get("team_names") or []),
                "cache",
            )
    if not api_key:
        return BrasileiraoMembership.empty(season)
    query = urllib.parse.urlencode({"league": BRASILEIRAO_SERIE_A_ID, "season": season})
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}?{query}",
        headers={"Accept": "application/json", "x-apisports-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload: dict[str, Any] = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"API-Football teams returned HTTP {error.code}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"API-Football teams request failed: {type(error).__name__}") from error
    membership = BrasileiraoMembership.from_api_response(season, payload)
    if not membership.team_names:
        raise RuntimeError(f"API-Football returned no Serie A teams for season {season}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_text(json.dumps(membership.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(cache_path)
    return membership
