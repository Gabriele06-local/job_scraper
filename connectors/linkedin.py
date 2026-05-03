"""LinkedIn connector — public guest API, language × keyword iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from config import settings
from scrapers.linkedin_scraper import LinkedInScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class LinkedInConnector(BaseConnector):
    source_name = "LinkedIn"
    source_type = SourceType.html
    rate_limit_seconds = 2.0

    def __init__(
        self,
        keywords: list[str] | None = None,
        languages: list[str] | None = None,
    ) -> None:
        self._scraper = LinkedInScraper(max_results=settings.linkedin_max_results)
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
                        "linkedin.fetched",
                        keyword=keyword,
                        lang=lang,
                        count=len(jobs),
                    )
                    yield from jobs
                except Exception as exc:
                    log.error(
                        "linkedin.fetch_error",
                        keyword=keyword,
                        lang=lang,
                        error=str(exc),
                    )
