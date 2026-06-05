"""JobList connector — Italian RSS feed (Informatica e Web category)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.italian_rss_scraper import ItalianRSSScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class JobListConnector(BaseConnector):
    source_name = "JobList"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = ItalianRSSScraper(
            feed_url="https://www.joblist.it/rss/informatica-e-web/",
            source_name="JobList",
        )

    def fetch(self) -> Iterator[dict]:
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            log.debug("joblist.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("joblist.fetch_error", error=str(exc))
