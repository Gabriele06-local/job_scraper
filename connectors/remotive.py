"""Remotive connector — REST API, no auth."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from scrapers.remotive_scraper import RemotiveScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class RemotiveConnector(BaseConnector):
    source_name = "Remotive"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = RemotiveScraper()

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("remotive.connector_error", error=str(exc))
