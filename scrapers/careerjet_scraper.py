"""CareerJet REST API scraper — keyword search with locale support."""

from __future__ import annotations

import time

import requests
import structlog
from utils.retry import safe_get

log = structlog.get_logger(__name__)

_BASE_URL = "https://public.api.careerjet.net/search"
_KEYWORDS = [
    "software engineer",
    "software developer",
    "web developer",
    "frontend",
    "backend",
    "fullstack",
    "devops",
    "mobile developer",
    "data scientist",
    "data engineer",
    "cloud engineer",
    "python",
    "javascript",
    "java",
    "programmatore",
    "sviluppatore",
]
_LOCALES = [
    "it_IT",
    "en_GB",
    "en_IE",
    "de_DE",
    "fr_FR",
    "es_ES",
    "nl_NL",
]
_RESULTS_PER_PAGE = 50
_MAX_PAGES = 5
_TIMEOUT = 15


class CareerJetScraper:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("careerjet.no_api_key")
            return []

        jobs: list[dict] = []
        seen_ids: set[str] = set()

        for locale in _LOCALES:
            country = locale.split("_")[1].lower()
            for keyword in _KEYWORDS:
                page = 1
                for _ in range(_MAX_PAGES):
                    try:
                        resp = safe_get(
                            _BASE_URL,
                            params={
                                "affid": self._api_key,
                                "keywords": keyword,
                                "location": country,
                                "sort": "date",
                                "page": page,
                                "pagesize": _RESULTS_PER_PAGE,
                                "user_ip": "0.0.0.0",
                                "user_agent": "DevBoardsCareerJetConnector/1.0",
                                "url": "https://devboards.io/jobs",
                            },
                            timeout=_TIMEOUT,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        if data.get("type") != "JOBS":
                            break
                        results = data.get("jobs", [])
                        if not results:
                            break
                        for item in results:
                            eid = item.get("id", "")
                            if eid in seen_ids:
                                continue
                            seen_ids.add(eid)
                            normalized = self._normalize(item, locale, keyword)
                            if normalized:
                                jobs.append(normalized)
                        total_pages = data.get("pages", 0)
                        if page >= total_pages:
                            break
                        page += 1
                        time.sleep(0.5)
                    except requests.RequestException as exc:
                        log.error(
                            "careerjet.fetch_error",
                            locale=locale,
                            keyword=keyword,
                            page=page,
                            error=str(exc),
                        )
                        break
        return jobs

    def _normalize(self, item: dict, locale: str, keyword: str) -> dict | None:
        title = item.get("title") or ""
        company = item.get("company") or ""
        url = item.get("url") or ""
        if not (title and company and url):
            return None
        sal_min = item.get("salary_min")
        sal_max = item.get("salary_max")
        currency_candidates = [item.get("currency"), "EUR"]
        currency = next((c for c in currency_candidates if c), "EUR")
        return {
            "title": title,
            "company_name": company,
            "description": item.get("description") or "",
            "url": url,
            "source": "CareerJet",
            "original_language": locale[:2],
            "published_at": item.get("date"),
            "location_raw": item.get("locations", [None])[0] if isinstance(item.get("locations"), list) else item.get("locations"),
            "salary_min": int(sal_min) if sal_min else None,
            "salary_max": int(sal_max) if sal_max else None,
            "currency": currency,
            "external_id": str(item.get("id", "")),
            "source_hints": {"keyword": keyword, "locale": locale},
        }
