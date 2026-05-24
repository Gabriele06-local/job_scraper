"""HN 'Who is Hiring' connector — RapidAPI by OdosUI."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.hn_hiring_scraper import HNHiringScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class HNHiringConnector(BaseConnector):
    source_name = "HN Who is Hiring"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = HNHiringScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("hn_hiring.connector_error", error=str(exc))
