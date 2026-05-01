"""Jobicy connector — remote jobs API, global source."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.jobicy_scraper import JobicyScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class JobicyConnector(BaseConnector):
    source_name = "Jobicy"
    source_type = SourceType.api
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = JobicyScraper()

    def fetch(self) -> Iterator[dict]:
        # Global remote source — one call returns up to 50 recent engineering jobs.
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="en"))
            log.debug("jobicy.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("jobicy.fetch_error", error=str(exc))
