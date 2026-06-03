"""AAAnnunci connector — Italian RSS feed (lavoro / informatica)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.italian_rss_scraper import ItalianRSSScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class AAAnnunciConnector(BaseConnector):
    source_name = "AAAnnunci"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = ItalianRSSScraper(
            feed_url="https://www.aaannunci.it/rss.aspx?cat=lavoro&subcat=informatica",
            source_name="AAAnnunci",
        )

    def fetch(self) -> Iterator[dict]:
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            log.debug("aaannunci.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("aaannunci.fetch_error", error=str(exc))
