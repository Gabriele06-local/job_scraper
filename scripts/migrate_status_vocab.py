"""One-shot migration: status vocab `valid`/`premium` → `active` + `quality_tier`.

SDD §D.1. **Idempotent**: re-runs are no-ops once every legacy doc has been
updated.

Run this BEFORE the backend ships the `status: {$ne: "expired"}` filter,
otherwise readers see legacy statuses (safe but inconsistent).

Usage:
    ./venv/bin/python -m scripts.migrate_status_vocab               # apply
    ./venv/bin/python -m scripts.migrate_status_vocab --dry-run     # report only

Connection: uses `config.settings.database_url` + `mongo_db` (the same
singleton as the rest of the scraper).
"""

from __future__ import annotations

import argparse
import sys

import structlog

from config import settings  # noqa: F401 — surface env load early
from database.repository import close_client, get_jobs

log = structlog.get_logger(__name__)


def _count_legacy(jobs_col) -> dict[str, int]:
    return {
        "valid": jobs_col.count_documents({"status": "valid"}),
        "premium": jobs_col.count_documents({"status": "premium"}),
    }


def _migrate(jobs_col, *, dry_run: bool) -> dict[str, int]:
    """Promote legacy `status=valid|premium` to `status=active` + tier.

    Idempotent: documents already on the new vocab don't match the filter so
    repeated runs do nothing.
    """
    before = _count_legacy(jobs_col)

    if dry_run:
        log.info("migrate.dry_run", would_update=before)
        return {"valid_updated": 0, "premium_updated": 0}

    valid_result = jobs_col.update_many(
        {"status": "valid"},
        {
            "$set": {
                "status": "active",
                "quality_tier": "valid",
                "quality.quality_tier": "valid",
            }
        },
    )
    premium_result = jobs_col.update_many(
        {"status": "premium"},
        {
            "$set": {
                "status": "active",
                "quality_tier": "premium",
                "quality.quality_tier": "premium",
            }
        },
    )
    return {
        "valid_updated": valid_result.modified_count,
        "premium_updated": premium_result.modified_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.migrate_status_vocab",
        description="Migrate legacy job.status vocab to SDD §I.4 (idempotent).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    jobs_col = get_jobs()

    log.info("migrate.start", dry_run=args.dry_run, before=_count_legacy(jobs_col))
    try:
        result = _migrate(jobs_col, dry_run=args.dry_run)
    finally:
        close_client()

    log.info("migrate.done", **result, after=_count_legacy(jobs_col))
    return 0


if __name__ == "__main__":
    sys.exit(main())
