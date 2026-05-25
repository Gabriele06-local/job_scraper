"""The Muse REST API scraper — requires free API key."""

from __future__ import annotations

import time

import requests
import structlog
from utils.retry import safe_get

log = structlog.get_logger(__name__)

_BASE_URL = "https://www.themuse.com/api/public/jobs"
_CATEGORIES = [
    "Software Engineer",
    "DevOps",
    "Data Science",
    "Product",
    "Design",
    "Project Management",
    "QA",
    "Security",
    "Mobile",
]
_LEVELS = ["Senior Level", "Mid Level", "Junior Level", "Management", "Internship"]
_TIMEOUT = 10


class TheMuseScraper:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("themuse.no_api_key")
            return []

        jobs: list[dict] = []
        seen_ids: set[str] = set()

        for category in _CATEGORIES:
            for level in _LEVELS:
                page = 0
                while True:
                    try:
                        resp = safe_get(
                            _BASE_URL,
                            params={
                                "api_key": self._api_key,
                                "category": category,
                                "level": level,
                                "page": page,
                            },
                            timeout=_TIMEOUT,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        results = data.get("results", [])
                        if not results:
                            break
                        for item in results:
                            jid = str(item.get("id", ""))
                            if jid in seen_ids:
                                continue
                            seen_ids.add(jid)
                            normalized = self._normalize(item)
                            if normalized:
                                jobs.append(normalized)
                        if page >= data.get("page_count", 1) - 1:
                            break
                        page += 1
                        time.sleep(0.3)
                    except requests.RequestException as exc:
                        log.error(
                            "themuse.fetch_error",
                            category=category,
                            level=level,
                            error=str(exc),
                        )
                        break
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        title = item.get("name") or ""
        company = (item.get("company") or {}).get("name") or ""
        url = (item.get("refs") or {}).get("landing_page") or ""
        if not (title and company and url):
            return None
        locations = item.get("locations", [])
        loc = locations[0].get("name") if locations else "Remote"
        return {
            "title": title,
            "company_name": company,
            "description": item.get("contents") or "",
            "url": url,
            "source": "TheMuse",
            "original_language": "en",
            "published_at": item.get("publication_date"),
            "location_raw": loc,
            "salary_min": None,
            "salary_max": None,
            "external_id": str(item.get("id", "")),
        }
