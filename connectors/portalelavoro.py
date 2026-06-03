"""PortaleLavoro connector — Italian RSS feed (WordPress)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.italian_rss_scraper import ItalianRSSScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class PortaleLavoroConnector(BaseConnector):
    source_name = "PortaleLavoro"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = ItalianRSSScraper(
            feed_url="https://www.portalelavoro.org/feed",
            source_name="PortaleLavoro",
        )

    def fetch(self) -> Iterator[dict]:
        try:
            all_jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            filtered = [j for j in all_jobs if "/lavoro/" in j.get("url", "")]
            skipped = len(all_jobs) - len(filtered)
            if skipped:
                log.info("portalelavoro.filtered", total=len(all_jobs), kept=len(filtered), skipped=skipped)
            yield from filtered
        except Exception as exc:
            log.error("portalelavoro.fetch_error", error=str(exc))
