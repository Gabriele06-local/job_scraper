"""E-Recruitment connector — Italian RSS feed."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.italian_rss_scraper import ItalianRSSScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class ERecruitmentConnector(BaseConnector):
    source_name = "E-Recruitment"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = ItalianRSSScraper(
            feed_url="https://www.e-recruitment.it/rss/Ultimi.php",
            source_name="E-Recruitment",
        )

    def fetch(self) -> Iterator[dict]:
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            log.debug("erecruitment.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("erecruitment.fetch_error", error=str(exc))
