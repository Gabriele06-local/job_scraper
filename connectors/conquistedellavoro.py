"""Conquiste del Lavoro connector — Italian RSS feed (Pubblico Impiego)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from scrapers.italian_rss_scraper import ItalianRSSScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class ConquisteDelLavoroConnector(BaseConnector):
    source_name = "ConquisteDelLavoro"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def __init__(self) -> None:
        self._scraper = ItalianRSSScraper(
            feed_url="http://www.conquistedellavoro.it/rss-pi-1.2430081",
            source_name="ConquisteDelLavoro",
        )

    def fetch(self) -> Iterator[dict]:
        try:
            jobs = asyncio.run(self._scraper.scrape(keyword="", lang="it"))
            log.debug("conquistedellavoro.fetched", count=len(jobs))
            yield from jobs
        except Exception as exc:
            log.error("conquistedellavoro.fetch_error", error=str(exc))
