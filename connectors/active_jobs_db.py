"""Active Jobs DB connector — RapidAPI by Fantastic.Jobs."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.active_jobs_db_scraper import ActiveJobsDbScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class ActiveJobsDbConnector(BaseConnector):
    source_name = "Active Jobs DB"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = ActiveJobsDbScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("active_jobs_db.connector_error", error=str(exc))
