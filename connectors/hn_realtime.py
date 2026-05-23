"""HN Real-Time Jobs connector — RapidAPI by Syed Abdulla."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.hn_realtime_scraper import HNRealtimeScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class HNRealtimeConnector(BaseConnector):
    source_name = "HN Real-Time Jobs"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = HNRealtimeScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("hn_realtime.connector_error", error=str(exc))
