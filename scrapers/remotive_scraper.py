"""Remotive REST API scraper — no auth required."""

from __future__ import annotations

import time

import requests
import structlog
from utils.retry import safe_get

log = structlog.get_logger(__name__)

_BASE_URL = "https://remotive.com/api/remote-jobs"
_CATEGORIES = [
    "software-dev",
    "devops-sysadmin",
    "data",
    "design",
    "product",
    "qa",
    "security",
]
_TIMEOUT = 10


class RemotiveScraper:
    def fetch(self) -> list[dict]:
        jobs: list[dict] = []
        for category in _CATEGORIES:
            try:
                resp = safe_get(
                    _BASE_URL,
                    params={"category": category},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("jobs", [])
                log.debug("remotive.fetched", category=category, count=len(raw))
                for item in raw:
                    normalized = self._normalize(item)
                    if normalized:
                        jobs.append(normalized)
            except requests.RequestException as exc:
                log.error("remotive.fetch_error", category=category, error=str(exc))
            time.sleep(0.5)
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        title = item.get("title") or ""
        company = item.get("company_name") or ""
        url = item.get("url") or ""
        if not (title and company and url):
            return None
        return {
            "title": title,
            "company_name": company,
            "description": item.get("description") or "",
            "url": url,
            "source": "Remotive",
            "original_language": "en",
            "published_at": item.get("publication_date"),
            "location_raw": item.get("candidate_required_location") or "Remote",
            "salary_min": None,
            "salary_max": None,
            "external_id": str(item.get("id", "")),
        }
