"""Himalayas REST API scraper — no auth required.

Must credit Himalayas as source in listings.
Max 20 jobs per request (server-enforced).
"""

from __future__ import annotations

import time

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://himalayas.app/jobs/api"
_LIMIT = 20
_MAX_PAGES = 10
_TIMEOUT = 10


class HimalayanScraper:
    def fetch(self) -> list[dict]:
        jobs: list[dict] = []
        for page in range(1, _MAX_PAGES + 1):
            try:
                resp = requests.get(
                    _BASE_URL,
                    params={"limit": _LIMIT, "page": page},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("jobs", [])
                if not raw:
                    break
                log.debug("himalayas.fetched", page=page, count=len(raw))
                for item in raw:
                    normalized = self._normalize(item)
                    if normalized:
                        jobs.append(normalized)
                if len(raw) < _LIMIT:
                    break
            except requests.RequestException as exc:
                log.error("himalayas.fetch_error", page=page, error=str(exc))
                break
            time.sleep(0.5)
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        title = item.get("title") or ""
        company = (item.get("company") or {}).get("name") or item.get("companyName") or ""
        url = item.get("url") or item.get("applicationUrl") or ""
        if not (title and company and url):
            return None
        return {
            "title": title,
            "company_name": company,
            "description": item.get("description") or "",
            "url": url,
            "source": "Himalayas",
            "original_language": "en",
            "published_at": item.get("publishedAt") or item.get("createdAt"),
            "location_raw": item.get("location") or "Remote",
            "salary_min": item.get("salaryMin") or item.get("salary_min"),
            "salary_max": item.get("salaryMax") or item.get("salary_max"),
            "external_id": str(item.get("id") or item.get("slug") or ""),
        }
