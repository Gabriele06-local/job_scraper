"""Active Jobs DB (RapidAPI by Fantastic.Jobs) scraper.

Covers 200k+ career sites & ATS (bamboohr, greenhouse, ashby, icims, workday,
jazzhr, jobvite, 20+ more). Hourly refresh, AI-enriched.
"""

from __future__ import annotations

import time
from datetime import datetime

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://active-jobs-db.p.rapidapi.com/active-ats-7d"
_RAPIDAPI_HOST = "active-jobs-db.p.rapidapi.com"
_TIMEOUT = 30
_PAGE_SIZE = 100
_MAX_PAGES = 5
_RATE_LIMIT_S = 0.5
_KEYWORDS = [
    "software engineer",
    "backend developer",
    "frontend developer",
    "fullstack developer",
    "devops engineer",
    "data engineer",
    "data scientist",
    "machine learning engineer",
]


def _first_str(item: dict, *keys: str) -> str:
    """Return the first non-empty string value found among keys."""
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _to_int(value: object) -> int | None:
    try:
        if value in (None, "", False):
            return None
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ActiveJobsDbScraper:
    """Scraper for the Active Jobs DB RapidAPI endpoint."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        """Paginate the endpoint per keyword, returning normalized RawJob dicts."""
        if not self._api_key:
            log.warning("active_jobs_db.no_api_key")
            return []

        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        jobs: list[dict] = []
        seen_ids: set[str] = set()

        for keyword in _KEYWORDS:
            for page in range(_MAX_PAGES):
                offset = page * _PAGE_SIZE
                try:
                    resp = requests.get(
                        _BASE_URL,
                        headers=headers,
                        params={
                            "limit": _PAGE_SIZE,
                            "offset": offset,
                            "advanced_title_filter": keyword,
                        },
                        timeout=_TIMEOUT,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    items = payload if isinstance(payload, list) else payload.get("data") or payload.get("jobs") or []
                    if not items:
                        break
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        eid = _first_str(item, "id", "job_id", "url")
                        if not eid or eid in seen_ids:
                            continue
                        seen_ids.add(eid)
                        normalized = self._normalize(item)
                        if normalized:
                            jobs.append(normalized)
                    time.sleep(_RATE_LIMIT_S)
                except requests.RequestException as exc:
                    log.error(
                        "active_jobs_db.fetch_error",
                        keyword=keyword,
                        page=page,
                        error=str(exc),
                    )
                    break

        log.info("active_jobs_db.fetch_complete", count=len(jobs))
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        """Map an Active Jobs DB item to the RawJob dict shape."""
        try:
            title = _first_str(item, "title", "job_title", "position")
            company = _first_str(
                item, "organization", "company", "company_name", "employer_name"
            )
            url = _first_str(item, "url", "job_url", "apply_url", "application_url")
            if not (title and company and url):
                return None

            location = _first_str(
                item, "location", "locations_derived", "city", "location_raw"
            )
            if not location:
                loc_obj = item.get("location_derived") or item.get("locations")
                if isinstance(loc_obj, list) and loc_obj:
                    location = str(loc_obj[0]) if loc_obj[0] else ""
                elif isinstance(loc_obj, dict):
                    parts = [str(loc_obj.get(k, "")) for k in ("city", "country")]
                    location = ", ".join(p for p in parts if p)

            posted_dt = _parse_iso(
                _first_str(item, "date_posted", "posted_at", "published_at", "created_at")
            )

            return {
                "title": title,
                "company_name": company,
                "description": _first_str(item, "description", "description_text"),
                "url": url,
                "source": "Active Jobs DB",
                "original_language": "en",
                "published_at": posted_dt,
                "location_raw": location or None,
                "salary_min": _to_int(item.get("salary_min") or item.get("min_salary")),
                "salary_max": _to_int(item.get("salary_max") or item.get("max_salary")),
                "currency": _first_str(item, "currency", "salary_currency") or None,
                "external_id": _first_str(item, "id", "job_id") or url,
            }
        except Exception as exc:  # noqa: BLE001 — schema is opaque; never crash pipeline
            log.warning("active_jobs_db.normalize_failed", error=str(exc))
            return None
