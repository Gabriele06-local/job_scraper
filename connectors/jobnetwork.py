"""JobNetwork connector — Italian RSS feed."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.jobnetwork_scraper import JobNetworkScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class JobNetworkConnector(BaseConnector):
    source_name = "JobNetwork"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        # /pub/rss.php is an HTML landing page; the real feed is homepage.xml,
        # which uses a custom <annuncio> schema (see JobNetworkScraper).
        self._scraper = JobNetworkScraper(
            feed_url="https://www.jobnetwork.it/rss/homepage.xml",
            source_name="JobNetwork",
        )

    def fetch(self) -> Iterator[dict]:
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            log.debug("jobnetwork.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("jobnetwork.fetch_error", error=str(exc))
