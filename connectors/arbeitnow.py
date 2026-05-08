"""Arbeitnow connector — global API, fetches all and filters client-side."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.arbeitnow_scraper import ArbeitnowScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class ArbeitnowConnector(BaseConnector):
    source_name = "Arbeitnow"
    source_type = SourceType.api
    # Scraper sleeps 5 s per attempt; honour that at connector level
    rate_limit_seconds = 5.0

    def __init__(self) -> None:
        self._scraper = ArbeitnowScraper()

    def fetch(self) -> Iterator[dict]:
        # Global source — one call with empty keyword returns all jobs.
        # Client-side filter in the scraper passes every item when keyword="".
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="en"))
            log.debug("arbeitnow.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("arbeitnow.fetch_error", error=str(exc))
