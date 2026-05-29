"""JSearch (RapidAPI) scraper — calls jsearch.p.rapidapi.com/search directly."""

from __future__ import annotations

import time
from datetime import datetime

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://jsearch.p.rapidapi.com/search"
_RAPIDAPI_HOST = "jsearch.p.rapidapi.com"
_TIMEOUT = 30
_MAX_PAGES = 5  # 10 results/page → ~50 per keyword
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
    """Scraper for the JSearch RapidAPI job aggregator."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        """Paginate the JSearch /search endpoint, returning normalized RawJob dicts."""
        if not self._api_key:
            log.warning("jsearch.no_api_key")
            return []

        from utils.retry import requests_retry

        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        jobs: list[dict] = []
        seen_ids: set[str] = set()

        @requests_retry
        def _fetch_page(keyword: str, page: int) -> list[dict]:
            resp = requests.get(
                _BASE_URL,
                headers=headers,
                params={
                    "query": keyword,
                    "page": page,
                    "num_pages": 1,
                    "date_posted": "month",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json().get("data") or []

        for keyword in _KEYWORDS:
            for page in range(1, _MAX_PAGES + 1):
                try:
                    items = _fetch_page(keyword, page)
                    if not items:
                        break
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
                    log.error(
                        "jsearch.fetch_error",
                        keyword=keyword,
                        page=page,
                        error=str(exc),
                    )
                    break

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

        # JSearch returns ISO timestamps with milliseconds ("2026-05-01T00:00:00.000Z");
        # the import-service CLI normalizer only matches 3 fixed strptime formats, so
        # parse to a tz-aware datetime here to land on the isinstance(datetime) branch.
        raw_posted = item.get("job_posted_at_datetime_utc") or ""
        posted_dt: datetime | None = None
        if raw_posted:
            try:
                posted_dt = datetime.fromisoformat(raw_posted.replace("Z", "+00:00"))
            except ValueError:
                pass

        return {
            "title": title,
            "company_name": company,
            "description": item.get("job_description") or "",
            "url": url,
            "source": "JSearch",
            "original_language": "en",
            "published_at": posted_dt,
            "location_raw": loc,
            "salary_min": int(sal_min) if sal_min else None,
            "salary_max": int(sal_max) if sal_max else None,
            "currency": item.get("job_salary_currency"),
            "external_id": str(item.get("job_id", "")),
        }
