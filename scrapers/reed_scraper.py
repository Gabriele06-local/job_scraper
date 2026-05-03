"""Reed.co.uk REST API scraper — HTTP Basic auth with API key."""

from __future__ import annotations

import time

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://www.reed.co.uk/api/1.0/search"
_KEYWORDS = [
    "developer",
    "devops",
    "sysadmin",
    "data engineer",
    "machine learning",
    "product manager",
    "UX designer",
    "security engineer",
    "QA engineer",
    "CTO",
    "tech lead",
    "software architect",
]
_RESULTS_PER_PAGE = 100
_MAX_PAGES = 10
_TIMEOUT = 10


class ReedScraper:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("reed.no_api_key")
            return []

        jobs: list[dict] = []
        seen_ids: set[int] = set()

        for keyword in _KEYWORDS:
            skip = 0
            for _ in range(_MAX_PAGES):
                try:
                    resp = requests.get(
                        _BASE_URL,
                        auth=(self._api_key, ""),
                        params={
                            "keywords": keyword,
                            "resultsToTake": _RESULTS_PER_PAGE,
                            "resultsToSkip": skip,
                        },
                        timeout=_TIMEOUT,
                    )
                    resp.raise_for_status()
                    results = resp.json().get("results", [])
                    if not results:
                        break
                    for item in results:
                        jid = item.get("jobId")
                        if jid in seen_ids:
                            continue
                        seen_ids.add(jid)
                        normalized = self._normalize(item)
                        if normalized:
                            jobs.append(normalized)
                    if len(results) < _RESULTS_PER_PAGE:
                        break
                    skip += _RESULTS_PER_PAGE
                    time.sleep(0.5)
                except requests.RequestException as exc:
                    log.error("reed.fetch_error", keyword=keyword, skip=skip, error=str(exc))
                    break
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        title = item.get("jobTitle") or ""
        company = item.get("employerName") or ""
        url = item.get("jobUrl") or ""
        if not (title and company and url):
            return None
        sal_min = item.get("minimumSalary")
        sal_max = item.get("maximumSalary")
        return {
            "title": title,
            "company_name": company,
            "description": item.get("jobDescription") or "",
            "url": url,
            "source": "Reed",
            "original_language": "en",
            "published_at": item.get("date"),
            "location_raw": item.get("locationName"),
            "salary_min": int(sal_min) if sal_min else None,
            "salary_max": int(sal_max) if sal_max else None,
            "currency": item.get("currency", "GBP"),
            "external_id": str(item.get("jobId", "")),
        }
