"""Ashby ATS connector — no auth required, includes compensation data."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import structlog

from scrapers.ashby_scraper import AshbyScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)

_REGISTRY_PATH = Path(__file__).parent / "ats_registry.json"


def _load_companies() -> list[dict[str, str]]:
    data = json.loads(_REGISTRY_PATH.read_text())
    return data.get("ashby", [])


class AshbyConnector(BaseConnector):
    source_name = "Ashby"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = AshbyScraper()
        self._companies = _load_companies()

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch(self._companies)
        except Exception as exc:
            log.error("ashby.connector_error", error=str(exc))
