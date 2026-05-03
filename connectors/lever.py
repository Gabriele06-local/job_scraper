"""Lever ATS connector — no auth required."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import structlog

from scrapers.lever_scraper import LeverScraper

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)

_REGISTRY_PATH = Path(__file__).parent / "ats_registry.json"


def _load_companies() -> list[dict[str, str]]:
    data = json.loads(_REGISTRY_PATH.read_text())
    return data.get("lever", [])


class LeverConnector(BaseConnector):
    source_name = "Lever"
    source_type = SourceType.api
    rate_limit_seconds = 0.5

    def __init__(self) -> None:
        self._scraper = LeverScraper()
        self._companies = _load_companies()

    def fetch(self) -> Iterator[dict]:
        try:
            yield from self._scraper.fetch(self._companies)
        except Exception as exc:
            log.error("lever.connector_error", error=str(exc))
