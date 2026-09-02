from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import FactoryConfig
from .media_delivery import build_delivery
from .pipeline import default_run_dir, run_all, run_phase1, run_phase2, run_phase3, run_phase4, run_phase5
from .preflight import run_preflight


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="jaguartv-prematch")
    commands = root.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser("preflight", help="Validate text routing without production writes")
    preflight.add_argument("--config", required=True, type=Path)
    preflight.add_argument("--dry-run", action="store_true")

    collect = commands.add_parser("collect", help="Collect and select tomorrow's featured fixtures")
    collect.add_argument("--config", required=True, type=Path)
    collect.add_argument("--output", required=True, type=Path)
    collect.add_argument("--fixtures-file", type=Path)

    for name in ("phase1", "phase2", "phase3", "phase4", "phase5", "run"):
        command = commands.add_parser(name, help=f"Run {name} for the pre-match workflow")
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--run-dir", type=Path)
        command.add_argument("--date")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--fixtures-file", type=Path)
        command.add_argument("--research-dir", type=Path)

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
        if report["production_ready"]:
            return 0
        dry_allowed = {"agent_reach_social", "image_primary", "image_fallback", "dreamina_vip_session"}
        blocked = {item["name"] for item in report["checks"] if item["status"] not in {"ok", "configured"}}
        return 0 if args.dry_run and blocked <= dry_allowed else 1

    if args.command == "collect":
        config = FactoryConfig.load(args.config)
        run_dir = args.output.parent if args.output.name == "phase1" else args.output
        print(json.dumps(run_phase1(config, run_dir, args.fixtures_file), ensure_ascii=False))
        return 0

    if args.command in {"phase1", "phase2", "phase3", "phase4", "phase5", "run"}:
        config = FactoryConfig.load(args.config)
        run_dir = args.run_dir or default_run_dir(args.date)
        try:
            if args.command == "phase1":
                result = run_phase1(config, run_dir, args.fixtures_file, args.date)
            elif args.command == "phase2":
                result = run_phase2(run_dir, dry_run=args.dry_run, research_dir=args.research_dir)
            elif args.command == "phase3":
                result = run_phase3(config, run_dir, dry_run=args.dry_run)
            elif args.command == "phase4":
                result = run_phase4(config, run_dir, dry_run=args.dry_run)
            elif args.command == "phase5":
                result = run_phase5(config, run_dir, dry_run=args.dry_run)
            else:
                result = run_all(config, run_dir, dry_run=args.dry_run, fixtures_file=args.fixtures_file, research_dir=args.research_dir, target_date=args.date)
        except Exception as error:
            print(json.dumps({"ok": False, "blocked_phase": args.command, "sanitized_error": str(error)[:800]}, ensure_ascii=False))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
