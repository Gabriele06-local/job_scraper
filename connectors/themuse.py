"""The Muse connector — REST API, requires THEMUSE_API_KEY."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.themuse_scraper import TheMuseScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class TheMuseConnector(BaseConnector):
    source_name = "TheMuse"
    source_type = SourceType.api
    rate_limit_seconds = 0.3

    def __init__(self) -> None:
        self._scraper = TheMuseScraper(api_key=settings.themuse_api_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("themuse.connector_error", error=str(exc))
