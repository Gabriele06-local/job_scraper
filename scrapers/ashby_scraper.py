"""Ashby ATS scraper — no auth required, includes compensation data."""

from __future__ import annotations

import time
from typing import Any

import requests
import structlog
from utils.retry import safe_get

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_TIMEOUT = 10


class AshbyScraper:
    def fetch(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for company in companies:
            slug = company["slug"]
            name = company["name"]
            try:
                resp = safe_get(
                    _BASE_URL.format(slug=slug),
                    params={"includeCompensation": "true"},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("jobs", [])
                log.debug("ashby.fetched", company=name, count=len(raw))
                for item in raw:
                    normalized = self._normalize(item, name)
                    if normalized:
                        jobs.append(normalized)
            except requests.RequestException as exc:
                log.error("ashby.fetch_error", company=name, error=str(exc)[:120])
            time.sleep(0.3)
        return jobs

    def _normalize(self, item: dict[str, Any], company_name: str) -> dict[str, Any] | None:
        title = item.get("title") or ""
        url = item.get("jobUrl") or ""
        if not (title and url):
            return None
        comp = item.get("compensation") or {}
        sal_min = comp.get("minValue") or comp.get("min")
        sal_max = comp.get("maxValue") or comp.get("max")
        currency = comp.get("currencyCode") or comp.get("currency")
        # Annualize if period is hourly or monthly
        period = (comp.get("interval") or "").lower()
        if sal_min and period == "hourly":
            sal_min = int(sal_min * 2080)
            sal_max = int(sal_max * 2080) if sal_max else None
        elif sal_min and period == "monthly":
            sal_min = int(sal_min * 12)
            sal_max = int(sal_max * 12) if sal_max else None
        return {
            "title": title,
            "company_name": company_name,
            "description": item.get("descriptionHtml") or item.get("description") or "",
            "url": url,
            "source": "Ashby",
            "original_language": "en",
            "published_at": item.get("publishedAt"),
            "location_raw": item.get("locationName") or item.get("location"),
            "salary_min": int(sal_min) if sal_min else None,
            "salary_max": int(sal_max) if sal_max else None,
            "currency": currency,
            "external_id": str(item.get("id", "")),
        }
