#!/usr/bin/env python3
"""Seed the `providers` collection with one document per connector slug.

Idempotent: upserts by `slug`. Existing `enabled` values are preserved on
re-runs (only catalog metadata — name, source_url, notes, pricing_tier,
requires_auth, auth_env_vars, credentials_present, geo, source_type — is
refreshed) so a backoffice toggle is not clobbered. New documents are inserted
with the `enabled` default declared in `_SEED`.

`credentials_present` is computed from the local environment at seed time
(the seed runs with the scraper's `.env` loaded), so the backoffice can flag
which paid/freemium sources still lack their API key.

Usage:
    python scripts/seed_providers.py             # dry-run (default)
    python scripts/seed_providers.py --confirm   # write to DB
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog  # noqa: E402

from config import settings  # noqa: E402
from database.repository import ensure_indexes, get_providers  # noqa: E402

log = structlog.get_logger(__name__)

PricingTier = Literal["free", "freemium", "metered"]
SourceType = Literal["api", "rss", "ats", "aggregator"]


@dataclass(frozen=True)
class ProviderSeed:
    """Static catalog entry for one connector slug.

    pricing_tier semantics (drives the backoffice cost view):
      - free:     no cost — open API or free key, never billed.
      - freemium: free tier today; a paid plan is required to scale beyond quota.
      - metered:  billed per job/call — actively capped to avoid surprise charges.
    """

    slug: str
    name: str
    pricing_tier: PricingTier
    source_type: SourceType
    geo: str
    source_url: str
    notes: str
    enabled_default: bool = True
    auth_env_vars: list[str] = field(default_factory=list)

    @property
    def requires_auth(self) -> bool:
        return bool(self.auth_env_vars)

    def credentials_present(self) -> bool:
        """True when every required key is non-empty in the loaded settings."""
        if not self.auth_env_vars:
            return True
        return all(
            bool(getattr(settings, var.lower(), "")) for var in self.auth_env_vars
        )


# Single source of truth for the provider catalog. Order is display order.
_SEED: list[ProviderSeed] = [
    # --- free, no auth ---
    ProviderSeed(
        "arbeitnow", "Arbeitnow", "free", "api", "DE/EU",
        "https://www.arbeitnow.com/api/job-board-api",
        "Free public API, EU-focused remote jobs.",
    ),
    ProviderSeed(
        "remoteok", "RemoteOK", "free", "api", "Worldwide",
        "https://remoteok.com/api",
        "Free public API, remote-only jobs.",
    ),
    ProviderSeed(
        "jobicy", "Jobicy", "free", "api", "Worldwide",
        "https://jobicy.com/jobs-rss-feed",
        "Free RSS-style API, remote jobs.",
    ),
    ProviderSeed(
        "remotive", "Remotive", "free", "api", "Worldwide",
        "https://remotive.com/api/remote-jobs",
        "Free public API, remote-only jobs.",
    ),
    ProviderSeed(
        "himalayas", "Himalayas", "free", "api", "Worldwide",
        "https://himalayas.app/jobs/api",
        "Free public API, remote-only jobs.",
    ),
    ProviderSeed(
        "rss", "RSS", "free", "rss", "EN",
        "",
        "Generic RSS aggregator across multiple configured feeds.",
    ),
    ProviderSeed(
        "iprogrammatori", "IProgrammatori", "free", "rss", "IT",
        "https://www.iprogrammatori.it/",
        "Free IT-focused RSS/HTML feed.",
    ),
    # --- free, ATS public job boards (no auth) ---
    ProviderSeed(
        "greenhouse", "Greenhouse", "free", "ats", "Global",
        "https://developers.greenhouse.io/job-board.html",
        "Public job-board endpoints scraped per known org (100 companies).",
    ),
    ProviderSeed(
        "lever", "Lever", "free", "ats", "Global",
        "https://hire.lever.co/developer/documentation",
        "Public job-board endpoints scraped per known org (100 companies).",
    ),
    ProviderSeed(
        "ashby", "Ashby", "free", "ats", "Global",
        "https://developers.ashbyhq.com/reference",
        "Public job-board endpoints scraped per known org (50 companies).",
    ),
    ProviderSeed(
        "personio", "Personio", "free", "ats", "EU",
        "https://developer.personio.de/",
        "Public job-board XML endpoints per known org (50 companies).",
    ),
    # --- free, RapidAPI key required (no billing on free tier) ---
    ProviderSeed(
        "hn_hiring", "HN Who is Hiring", "free",
        "aggregator", "Worldwide",
        "https://rapidapi.com/odosui/api/hacker-news-who-is-hiring-api/",
        "Monthly HN 'Who is Hiring' thread. Free tier, high senior-dev signal.",
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    ProviderSeed(
        "yc_jobs", "Y Combinator Jobs", "free",
        "aggregator", "Worldwide",
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/free-y-combinator-jobs-api/",
        "YC-backed companies only. Free tier, refreshed twice daily.",
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    ProviderSeed(
        "faang_watch", "faang.watch", "free",
        "aggregator", "Worldwide",
        "https://rapidapi.com/local-transformer/api/faang-watch-api/",
        "FAANG career pages unified (Google, Meta, Apple, Amazon, Netflix). Free tier.",
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    ProviderSeed(
        "hn_realtime", "HN Real-Time Jobs", "free",
        "aggregator", "Worldwide",
        "https://rapidapi.com/syed-abdulla/api/hacker-news-real-time-jobs-startup-hiring-api/",
        "Real-time HN job postings with company/location extraction. Free tier.",
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    # --- freemium: free tier now, paid plan required to scale ---
    ProviderSeed(
        "careerjet", "CareerJet", "freemium", "api",
        "IT,ES,FR,DE,GB,IE,NL",
        "https://www.careerjet.com/partners/api/",
        "International job search engine. Free tier covers 7 European locales "
        "including Italy (it_IT). Paid plan to lift quota.",
        auth_env_vars=["CAREERJET_API_KEY"],
    ),
    ProviderSeed(
        "adzuna", "Adzuna", "freemium", "api",
        "gb,us,de,nl,fr,au,ca,at,be,nz",
        "https://developer.adzuna.com/",
        "Free tier ~240 calls/day (budget-capped). Paid plan to lift quota.",
        auth_env_vars=["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
    ),
    ProviderSeed(
        "jooble", "Jooble", "freemium", "api", "Configurable",
        "https://jooble.org/api/about",
        "Keyword search. Free tier available; paid plan to scale.",
        auth_env_vars=["JOOBLE_API_KEY"],
    ),
    ProviderSeed(
        "reed", "Reed", "freemium", "api", "GB",
        "https://www.reed.co.uk/developers",
        "UK market, HTTP Basic auth. Free tier available.",
        auth_env_vars=["REED_API_KEY"],
    ),
    ProviderSeed(
        "themuse", "TheMuse", "freemium", "api", "US/Global",
        "https://www.themuse.com/developers/api/v2",
        "Free account key; rate-limited free tier.",
        auth_env_vars=["THEMUSE_API_KEY"],
    ),
    ProviderSeed(
        "jsearch", "JSearch", "freemium", "aggregator", "Worldwide",
        "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch/",
        "Aggregates Indeed, LinkedIn, Glassdoor, ZipRecruiter, Monster. "
        "Free tier limited; paid plan to scale.",
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    # --- metered: billed per job returned (Fantastic.Jobs) — budget-capped ---
    ProviderSeed(
        "active_jobs_db", "Active Jobs DB", "metered",
        "aggregator", "Worldwide",
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/active-jobs-db/",
        "200k+ career sites & ATS, AI-enriched. Billed per job — capped by "
        "RAPIDAPI_MONTHLY_JOB_BUDGET.",
        enabled_default=False,
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    ProviderSeed(
        "workday_jobs", "Workday Jobs", "metered",
        "aggregator", "Worldwide",
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/workday-jobs-api/",
        "4k+ Workday career sites, enterprise-heavy. Billed per job — "
        "capped by RAPIDAPI_MONTHLY_JOB_BUDGET.",
        enabled_default=False,
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
    ProviderSeed(
        "startup_jobs", "Startup Jobs", "metered",
        "aggregator", "Worldwide",
        "https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/startup-jobs-api/",
        "Wellfound (AngelList), YC, LinkedIn, AshbyHQ. Billed per job — "
        "capped by RAPIDAPI_MONTHLY_JOB_BUDGET.",
        enabled_default=False,
        auth_env_vars=["RAPIDAPI_KEY"],
    ),
]


def _catalog_fields(p: ProviderSeed, now: datetime) -> dict:
    """Catalog metadata refreshed on every upsert (never includes `enabled`)."""
    return {
        "name": p.name,
        "pricing_tier": p.pricing_tier,
        "source_type": p.source_type,
        "geo": p.geo,
        "source_url": p.source_url,
        "notes": p.notes,
        "requires_auth": p.requires_auth,
        "auth_env_vars": p.auth_env_vars,
        "credentials_present": p.credentials_present(),
        "updated_at": now,
    }


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
        for p in _SEED:
            log.info(
                "seed_providers.would_upsert",
                slug=p.slug,
                name=p.name,
                pricing_tier=p.pricing_tier,
                requires_auth=p.requires_auth,
                credentials_present=p.credentials_present(),
                enabled_default=p.enabled_default,
            )
        log.info("seed_providers.dry_run_complete")
        return 0

    ensure_indexes()
    col = get_providers()
    now = datetime.now(tz=timezone.utc)
    inserted = 0
    updated = 0

    for p in _SEED:
        catalog = _catalog_fields(p, now)
        existing = col.find_one({"slug": p.slug})
        if existing is None:
            col.insert_one(
                {
                    "slug": p.slug,
                    "enabled": p.enabled_default,
                    "created_at": now,
                    **catalog,
                }
            )
            inserted += 1
            log.info("seed_providers.inserted", slug=p.slug, enabled=p.enabled_default)
        else:
            col.update_one({"slug": p.slug}, {"$set": catalog})
            updated += 1
            log.info(
                "seed_providers.updated",
                slug=p.slug,
                enabled=existing.get("enabled"),
            )

    log.info("seed_providers.complete", inserted=inserted, updated=updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
