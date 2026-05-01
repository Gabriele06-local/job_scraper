"""ReteInformaticaLavoro connector — Italian HTML, keyword-based pagination."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from config import settings
from scrapers.reteinformaticalavoro_scraper import ReteInformaticaLavoroScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class ReteInformaticaLavoroConnector(BaseConnector):
    source_name = "ReteInformaticaLavoro"
    source_type = SourceType.html
    rate_limit_seconds = 0.4

    def __init__(self, keywords: list[str] | None = None) -> None:
        self._scraper = ReteInformaticaLavoroScraper()
        # IT-only source; no need for per-language iteration
        self._keywords = keywords or settings.scrape_keywords

    def fetch(self) -> Iterator[dict]:
        for keyword in self._keywords:
            try:
                jobs = asyncio.run(self._scraper.scrape(keyword=keyword, lang="it"))
                log.debug(
                    "reteinformaticalavoro.fetched",
                    keyword=keyword,
                    count=len(jobs),
                )
                yield from jobs
            except Exception as exc:
                log.error(
                    "reteinformaticalavoro.fetch_error",
                    keyword=keyword,
                    error=str(exc),
                )
