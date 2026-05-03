"""Himalayas connector — REST API, no auth. Credit Himalayas as source."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from scrapers.himalayas_scraper import HimalayanScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class HimalayasConnector(BaseConnector):
    source_name = "Himalayas"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = HimalayanScraper()

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("himalayas.connector_error", error=str(exc))
