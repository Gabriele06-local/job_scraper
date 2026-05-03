"""IProgrammatori connector — Italian RSS, fetches all IT jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.iprogrammatori_scraper import IProgrammatoriScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class IProgrammatoriConnector(BaseConnector):
    source_name = "IProgrammatori"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = IProgrammatoriScraper()

    def fetch(self) -> Iterator[dict]:
        # IT-only source — one fetch with empty keyword returns all available jobs.
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            log.debug("iprogrammatori.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("iprogrammatori.fetch_error", error=str(exc))
