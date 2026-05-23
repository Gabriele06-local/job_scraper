"""Startup Jobs connector — RapidAPI by Fantastic.Jobs."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from config import settings
from scrapers.startup_jobs_scraper import StartupJobsScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class StartupJobsConnector(BaseConnector):
    source_name = "Startup Jobs"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = StartupJobsScraper(api_key=settings.rapidapi_key)

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch()
        except Exception as exc:
            log.error("startup_jobs.connector_error", error=str(exc))
