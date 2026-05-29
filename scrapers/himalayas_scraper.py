"""Himalayas REST API scraper — no auth required.

Must credit Himalayas as source in listings.
Max 20 jobs per request (server-enforced).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests
import structlog
from utils.retry import safe_get

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
                resp = safe_get(
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
        # Schema (2026-03): companyName, applicationLink, pubDate. Old keys
        # (company.name/applicationUrl/publishedAt) kept as fallbacks.
        company = (
            item.get("companyName") or (item.get("company") or {}).get("name") or ""
        )
        url = (
            item.get("applicationLink") or item.get("applicationUrl") or item.get("url") or ""
        )
        if not (title and company and url):
            return None

        # pubDate is a unix epoch (seconds or ms); convert so the downstream
        # normalizer records the real posting date instead of falling back to now().
        published = item.get("pubDate") or item.get("publishedAt") or item.get("createdAt")
        if isinstance(published, (int, float)):
            ts = published / 1000 if published > 1e11 else published
            try:
                published = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except (ValueError, OSError):
                published = None

        loc = item.get("locationRestrictions") or item.get("location")
        if isinstance(loc, list):
            loc = ", ".join(str(x) for x in loc if x) or "Remote"
        location_raw = loc or "Remote"

        return {
            "title": title,
            "company_name": company,
            "description": item.get("description") or item.get("excerpt") or "",
            "url": url,
            "source": "Himalayas",
            "original_language": "en",
            "published_at": published,
            "location_raw": location_raw,
            "salary_min": item.get("minSalary") or item.get("salaryMin") or item.get("salary_min"),
            "salary_max": item.get("maxSalary") or item.get("salaryMax") or item.get("salary_max"),
            "currency": item.get("currency"),
            "external_id": str(item.get("guid") or item.get("id") or item.get("slug") or ""),
        }
