"""RSS connector — multi-feed scraper, one fetch per language."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from config import settings
from scrapers.rss_scraper import RSSScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)

# Default feeds — empty keyword passes all items through client-side filter.
_DEFAULT_RSS_URLS: dict[str, list[str]] = {
    "en": [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://jobicy.com/feed",
    ],
}


class RSSConnector(BaseConnector):
    source_name = "RSS"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(
        self,
        rss_urls: dict[str, list[str]] | None = None,
        languages: list[str] | None = None,
    ) -> None:
        self._scraper = RSSScraper(rss_urls=rss_urls or _DEFAULT_RSS_URLS)
        # Only iterate over langs that have configured feeds
        configured_langs = list((rss_urls or _DEFAULT_RSS_URLS).keys())
        requested_langs = languages or settings.scrape_languages
        self._languages = [lg for lg in requested_langs if lg in configured_langs]

    def fetch(self) -> Iterator[dict]:
        for lang in self._languages:
            try:
                jobs = asyncio.run(self._scraper.scrape(keyword="", lang=lang))
                log.debug("rss.fetched", lang=lang, count=len(jobs))
                yield from jobs
            except Exception as exc:
                log.error("rss.fetch_error", lang=lang, error=str(exc))
