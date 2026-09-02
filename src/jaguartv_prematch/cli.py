from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .collector import collect_fixtures, save_collection, tomorrow_brasilia
from .config import FactoryConfig
from .media_delivery import build_delivery
from .preflight import run_preflight
from .selection import select_fixtures


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="jaguartv-prematch")
    commands = root.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser("preflight", help="Validate text routing without production writes")
    preflight.add_argument("--config", required=True, type=Path)

    collect = commands.add_parser("collect", help="Collect and select tomorrow's featured fixtures")
    collect.add_argument("--config", required=True, type=Path)
    collect.add_argument("--output", required=True, type=Path)

    delivery = commands.add_parser("media-delivery", help="Build the completed-media delivery folder")
    delivery.add_argument("--destination", required=True, type=Path)
    delivery.add_argument("--poster-root", action="append", type=Path, default=[])
    delivery.add_argument("--video-root", action="append", type=Path, default=[])
    delivery.add_argument("--cta-root", action="append", type=Path, default=[])
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "preflight":
        config = FactoryConfig.load(args.config)
        repository = Path(__file__).resolve().parents[2]
        report = run_preflight(config, repository)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["production_ready"] else 1

    if args.command == "collect":
        config = FactoryConfig.load(args.config)
        collector = config.data["collector"]
        api_key = config.env_value(collector, "api_key_env", required=False)
        fixtures, raw = collect_fixtures(
            collector["base_url"], api_key=api_key,
            timeout_seconds=int(collector.get("timeout_seconds", 30)),
        )
        save_collection(fixtures, raw, args.output / "fixtures.json")
        selected = {
            "date": tomorrow_brasilia(),
            "timezone_label": "Horário de Brasília",
            "fixtures": select_fixtures(fixtures),
        }
        (args.output / "selected-fixtures.json").write_text(
            json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"collected": len(fixtures), "selected": len(selected["fixtures"])}, ensure_ascii=False))
        return 0

    if args.command == "media-delivery":
        manifest = build_delivery(
            args.destination,
            poster_roots=args.poster_root,
            video_roots=args.video_root,
            cta_roots=args.cta_root,
        )
        print(json.dumps(manifest["counts"], ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
