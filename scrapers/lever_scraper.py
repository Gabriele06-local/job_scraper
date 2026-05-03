"""Lever ATS scraper — no auth required."""

from __future__ import annotations

import time
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.lever.co/v0/postings/{slug}"
_TIMEOUT = 10


class LeverScraper:
    def fetch(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for company in companies:
            slug = company["slug"]
            name = company["name"]
            try:
                resp = requests.get(
                    _BASE_URL.format(slug=slug),
                    params={"mode": "json"},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                raw = resp.json()
                if isinstance(raw, list):
                    for item in raw:
                        normalized = self._normalize(item, name)
                        if normalized:
                            jobs.append(normalized)
                log.debug("lever.fetched", company=name, count=len(raw) if isinstance(raw, list) else 0)
            except requests.RequestException as exc:
                log.error("lever.fetch_error", company=name, error=str(exc)[:120])
            time.sleep(0.3)
        return jobs

    def _normalize(self, item: dict[str, Any], company_name: str) -> dict[str, Any] | None:
        title = item.get("text") or ""
        url = item.get("hostedUrl") or ""
        if not (title and url):
            return None
        cats = item.get("categories") or {}
        location = cats.get("location") or cats.get("team") or ""
        sal = item.get("salaryRange") or {}
        return {
            "title": title,
            "company_name": company_name,
            "description": item.get("descriptionPlain") or item.get("description") or "",
            "url": url,
            "source": "Lever",
            "original_language": "en",
            "published_at": item.get("createdAt"),
            "location_raw": location,
            "salary_min": sal.get("min"),
            "salary_max": sal.get("max"),
            "currency": sal.get("currency"),
            "external_id": str(item.get("id", "")),
        }
