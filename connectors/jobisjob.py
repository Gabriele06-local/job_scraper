"""JobisJob connector — HTML scraper, language × keyword iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from config import settings
from scrapers.jobisjob_scraper import JobisJobScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class JobisJobConnector(BaseConnector):
    source_name = "JobisJob"
    source_type = SourceType.html
    rate_limit_seconds = 1.0

    def __init__(
        self,
        keywords: list[str] | None = None,
        languages: list[str] | None = None,
    ) -> None:
        self._scraper = JobisJobScraper()
        self._keywords = keywords or settings.scrape_keywords
        self._languages = languages or settings.scrape_languages

    def fetch(self) -> Iterator[dict]:
        for lang in self._languages:
            for keyword in self._keywords:
                try:
                    jobs = asyncio.run(
                        self._scraper.scrape(keyword=keyword, lang=lang)
                    )
                    log.debug(
                        "jobisjob.fetched",
                        keyword=keyword,
                        lang=lang,
                        count=len(jobs),
                    )
                    yield from jobs
                except Exception as exc:
                    log.error(
                        "jobisjob.fetch_error",
                        keyword=keyword,
                        lang=lang,
                        error=str(exc),
                    )
