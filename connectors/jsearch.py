"""JSearch connector — wraps JSearchScraper (RapidAPI) for the import pipeline."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.jsearch_scraper import JSearchScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class JSearchConnector(BaseConnector):
    source_name = "JSearch"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = JSearchScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("jsearch.connector_error", error=str(exc))
