"""Y Combinator Jobs connector — RapidAPI by Fantastic.Jobs."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.yc_jobs_scraper import YCJobsScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class YCJobsConnector(BaseConnector):
    source_name = "Y Combinator Jobs"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = YCJobsScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("yc_jobs.connector_error", error=str(exc))
