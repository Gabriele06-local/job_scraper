"""JSearch scraper — fetches via Apify community actor (mhrynenko/jsearch-scraper)."""

from __future__ import annotations

import time

import requests
import structlog

log = structlog.get_logger(__name__)

_ENDPOINT = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
_ACTOR_ID = "mhrynenko~jsearch-scraper"
_TIMEOUT = 300
_MAX_ITEMS_PER_QUERY = 50
_KEYWORDS = [
    "software engineer",
    "backend developer",
    "frontend developer",
    "fullstack developer",
    "devops engineer",
    "data engineer",
    "data scientist",
    "machine learning engineer",
    "qa engineer",
    "mobile developer",
]


class JSearchScraper:
    """Scraper for JSearch jobs proxied via Apify run-sync actor."""

    def __init__(self, api_token: str = "") -> None:
        self._api_token = api_token

    def fetch(self) -> list[dict]:
        """Run the Apify actor once per keyword, returning normalized RawJob dicts."""
        if not self._api_token:
            log.warning("jsearch.no_api_token")
            return []

        jobs: list[dict] = []
        seen_ids: set[str] = set()
        url = _ENDPOINT.format(actor=_ACTOR_ID)

        for keyword in _KEYWORDS:
            try:
                resp = requests.post(
                    url,
                    params={"token": self._api_token},
                    json={
                        "queries": [keyword],
                        "maxItems": _MAX_ITEMS_PER_QUERY,
                        "country": "us",
                        "language": "en",
                        "datePosted": "month",
                    },
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                items = resp.json() or []
                for item in items:
                    jid = str(item.get("job_id") or "")
                    if not jid or jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    normalized = self._normalize(item)
                    if normalized:
                        jobs.append(normalized)
                time.sleep(0.5)
            except requests.RequestException as exc:
                log.error("jsearch.fetch_error", keyword=keyword, error=str(exc))
                continue

        log.info("jsearch.fetch_complete", count=len(jobs))
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        """Map a JSearch item to the RawJob dict shape used by the pipeline."""
        title = item.get("job_title") or ""
        company = item.get("employer_name") or ""
        url = item.get("job_apply_link") or ""
        if not (title and company and url):
            return None

        city = item.get("job_city") or ""
        country = item.get("job_country") or ""
        loc = ", ".join(p for p in (city, country) if p) or None

        sal_min = item.get("job_min_salary")
        sal_max = item.get("job_max_salary")

        return {
            "title": title,
            "company_name": company,
            "description": item.get("job_description") or "",
            "url": url,
            "source": "JSearch",
            "original_language": "en",
            "published_at": item.get("job_posted_at_datetime_utc"),
            "location_raw": loc,
            "salary_min": int(sal_min) if sal_min else None,
            "salary_max": int(sal_max) if sal_max else None,
            "currency": item.get("job_salary_currency"),
            "external_id": str(item.get("job_id", "")),
        }
