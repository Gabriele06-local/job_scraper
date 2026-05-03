"""Reed connector — REST API, requires REED_API_KEY."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.reed_scraper import ReedScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class ReedConnector(BaseConnector):
    source_name = "Reed"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = ReedScraper(api_key=settings.reed_api_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("reed.connector_error", error=str(exc))
