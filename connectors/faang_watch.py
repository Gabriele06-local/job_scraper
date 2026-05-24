"""faang.watch connector — RapidAPI by Local Transformer."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.faang_watch_scraper import FaangWatchScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class FaangWatchConnector(BaseConnector):
    source_name = "faang.watch"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = FaangWatchScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("faang_watch.connector_error", error=str(exc))
