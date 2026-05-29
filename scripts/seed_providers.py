#!/usr/bin/env python3
"""Seed the `providers` collection with one document per connector slug.

Idempotent: upserts by `slug`. Existing `enabled` values are preserved on
re-runs (only `name`, `source_url`, `notes`, and `updated_at` are refreshed)
so a backoffice toggle is not clobbered. New documents are inserted with the
`enabled` default declared in `_SEED`.

Usage:
    python scripts/seed_providers.py             # dry-run (default)
    python scripts/seed_providers.py --confirm   # write to DB
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog  # noqa: E402

from database.repository import ensure_indexes, get_providers  # noqa: E402

log = structlog.get_logger(__name__)


# (slug, name, enabled_default, source_url, notes)
_SEED: list[tuple[str, str, bool, str, str]] = [
    # --- existing connectors (legacy: enabled by default) ---
    (
        "adzuna",
        "Adzuna",
        True,
        "https://developer.adzuna.com/",
        "Requires ADZUNA_APP_ID + ADZUNA_APP_KEY. Free tier available.",
    ),
    (
        "arbeitnow",
        "Arbeitnow",
        True,
        "https://www.arbeitnow.com/api/job-board-api",
        "Free public API, EU-focused remote jobs.",
    ),
    (
        "ashby",
        "Ashby",
        True,
        "https://developers.ashbyhq.com/reference",
        "Public job-board endpoints scraped per known org.",
    ),
    (
        "greenhouse",
        "Greenhouse",
        True,
        "https://developers.greenhouse.io/job-board.html",
        "Public job-board endpoints scraped per known org.",
    ),
    (
        "himalayas",
        "Himalayas",
        True,
        "https://himalayas.app/jobs/api",
        "Free public API, remote-only jobs.",
    ),
    (
        "iprogrammatori",
        "iProgrammatori",
        True,
        "https://www.iprogrammatori.it/",
        "HTML scrape, IT-focused.",
    ),
    (
        "jobicy",
        "Jobicy",
        True,
        "https://jobicy.com/jobs-rss-feed",
        "Free RSS-style API, remote jobs.",
    ),
    (
        "jooble",
        "Jooble",
        True,
        "https://jooble.org/api/about",
        "Requires JOOBLE_API_KEY. Free tier available.",
    ),
    (
        "jsearch",
        "JSearch (RapidAPI)",
        True,
        "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch/",
        "RapidAPI: aggregates Indeed, LinkedIn, Glassdoor, ZipRecruiter, Monster. Requires RAPIDAPI_KEY.",
    ),
    (
        "lever",
        "Lever",
        True,
        "https://hire.lever.co/developer/documentation",
        "Public job-board endpoints scraped per known org.",
    ),
    (
        "personio",
        "Personio",
        True,
        "https://developer.personio.de/",
        "Public job-board XML endpoints per known org.",
    ),
    (
        "reed",
        "Reed",
        True,
        "https://www.reed.co.uk/developers",
        "Requires REED_API_KEY. UK-focused.",
    ),
    (
        "remoteok",
        "RemoteOK",
        True,
        "https://remoteok.com/api",
        "Free public API, remote-only jobs.",
    ),
    (
        "remotive",
        "Remotive",
        True,
        "https://remotive.com/api/remote-jobs",
        "Free public API, remote-only jobs.",
    ),
    (
        "rss",
        "RSS feeds",
        True,
        "",
        "Generic RSS aggregator across multiple configured feeds.",
    ),
    (
        "themuse",
        "The Muse",
        True,
        "https://www.themuse.com/developers/api/v2",
        "Requires THEMUSE_API_KEY. Free tier available.",
    ),
    # --- RapidAPI connectors (requires RAPIDAPI_KEY) ---
    (
        "active_jobs_db",
        "Active Jobs DB (RapidAPI)",
        True,
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/active-jobs-db/",
        "Requires RAPIDAPI_KEY. 200k+ career sites & ATS (bamboohr, greenhouse, ashby, icims, workday, jazzhr, jobvite, 20+ more). Hourly refresh, AI-enriched.",
    ),
    (
        "workday_jobs",
        "Workday Jobs (RapidAPI)",
        True,
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/workday-jobs-api/",
        "Requires RAPIDAPI_KEY. 4k+ Workday career sites; enterprise-heavy; hourly refresh.",
    ),
    (
        "startup_jobs",
        "Startup Jobs (RapidAPI)",
        True,
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/startup-jobs-api/",
        "Requires RAPIDAPI_KEY. Wellfound (AngelList), YC companies, LinkedIn, AshbyHQ — ideal for dev-focused startup roles.",
    ),
    (
        "hn_hiring",
        "Hacker News Who Is Hiring (RapidAPI)",
        True,
        "https://rapidapi.com/odosui/api/hacker-news-who-is-hiring-api/",
        "Requires RAPIDAPI_KEY. Monthly HN 'Who is Hiring' thread. Free, high senior dev signal.",
    ),
    (
        "yc_jobs",
        "Y Combinator Jobs (RapidAPI)",
        True,
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/free-y-combinator-jobs-api/",
        "Requires RAPIDAPI_KEY. YC-backed companies only. Free, refreshed twice daily.",
    ),
    (
        "faang_watch",
        "faang.watch (RapidAPI)",
        True,
        "https://rapidapi.com/local-transformer/api/faang-watch-api/",
        "Requires RAPIDAPI_KEY. FAANG career pages unified (Google, Meta, Apple, Amazon, Netflix). All roles are tech.",
    ),
    (
        "hn_realtime",
        "Hacker News Real-Time Jobs (RapidAPI)",
        True,
        "https://rapidapi.com/syed-abdulla/api/hacker-news-real-time-jobs-startup-hiring-api/",
        "Requires RAPIDAPI_KEY. Real-time HN job postings with company/location extraction. Complements hn_hiring.",
    ),
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/seed_providers.py",
        description="Seed the `providers` collection (idempotent upsert).",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dry_run = (not args.confirm) or args.dry_run

    log.info("seed_providers.start", dry_run=dry_run, count=len(_SEED))

    if dry_run:
        for slug, name, enabled, src, notes in _SEED:
            log.info(
                "seed_providers.would_upsert",
                slug=slug,
                name=name,
                enabled_default=enabled,
                source_url=src,
                notes=notes[:80],
            )
        log.info("seed_providers.dry_run_complete")
        return 0

    ensure_indexes()
    col = get_providers()
    now = datetime.now(tz=timezone.utc)
    inserted = 0
    updated = 0

    for slug, name, enabled_default, source_url, notes in _SEED:
        existing = col.find_one({"slug": slug})
        if existing is None:
            col.insert_one(
                {
                    "slug": slug,
                    "name": name,
                    "enabled": enabled_default,
                    "source_url": source_url,
                    "notes": notes,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            inserted += 1
            log.info(
                "seed_providers.inserted",
                slug=slug,
                enabled=enabled_default,
            )
        else:
            col.update_one(
                {"slug": slug},
                {
                    "$set": {
                        "name": name,
                        "source_url": source_url,
                        "notes": notes,
                        "updated_at": now,
                    }
                },
            )
            updated += 1
            log.info(
                "seed_providers.updated",
                slug=slug,
                enabled=existing.get("enabled"),
            )

    log.info("seed_providers.complete", inserted=inserted, updated=updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
