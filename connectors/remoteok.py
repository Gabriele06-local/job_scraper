"""RemoteOK connector — global JSON API, one fetch returns all recent jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.remoteok_scraper import RemoteOKScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class RemoteOKConnector(BaseConnector):
    source_name = "RemoteOK"
    source_type = SourceType.api
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = RemoteOKScraper()

    def fetch(self) -> Iterator[dict]:
        # Global source — one call returns all recent remote jobs.
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="en"))
            log.debug("remoteok.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("remoteok.fetch_error", error=str(exc))
