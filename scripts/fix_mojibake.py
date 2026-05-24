#!/usr/bin/env python3
"""One-shot migration: repair mojibake (double-encoded UTF-8) in jobs DB.

Thin CLI wrapper around `pipeline.mojibake_migration.run`. The same
function is auto-invoked once per environment at the start of
`python -m import_service.cli import`, so stage and prod self-heal on
first boot. Use this script for manual dry-runs or forced reruns.

Usage:
    python scripts/fix_mojibake.py             # dry-run (default)
    python scripts/fix_mojibake.py --confirm   # write to DB
    python scripts/fix_mojibake.py --confirm --rerun  # ignore done-marker
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog  # noqa: E402

from pipeline.mojibake_migration import run  # noqa: E402

log = structlog.get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/fix_mojibake.py",
        description="Repair mojibake in the jobs/companies collections.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run (default when --confirm is absent).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Write to DB. Without it, the script logs intended writes only.",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Ignore the `migrations` guard and execute even if already marked done.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dry_run = (not args.confirm) or args.dry_run
    run(dry_run=dry_run, rerun=args.rerun)
    return 0


if __name__ == "__main__":
    sys.exit(main())
