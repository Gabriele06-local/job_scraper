"""Adzuna connector — REST API, keyword × language iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from config import settings
from scrapers.adzuna_scraper import AdzunaScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class AdzunaConnector(BaseConnector):
    source_name = "Adzuna"
    source_type = SourceType.api
    rate_limit_seconds = 1.0

    def __init__(
        self,
        keywords: list[str] | None = None,
        countries: list[str] | None = None,
    ) -> None:
        self._scraper = AdzunaScraper(
            app_id=settings.adzuna_app_id,
            app_key=settings.adzuna_app_key,
        )
        self._keywords = keywords or settings.scrape_keywords
        self._countries = countries or AdzunaScraper.COUNTRIES

    def fetch(self) -> Iterator[dict]:
        for country in self._countries:
            for keyword in self._keywords:
                try:
                    jobs = asyncio.run(
                        self._scraper.scrape(keyword=keyword, country=country)
                    )
                    log.debug(
                        "adzuna.fetched",
                        keyword=keyword,
                        country=country,
                        count=len(jobs),
                    )
                    yield from jobs
                except Exception as exc:
                    log.error(
                        "adzuna.fetch_error",
                        keyword=keyword,
                        country=country,
                        error=str(exc),
                    )
