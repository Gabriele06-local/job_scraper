"""Free Y Combinator Jobs API (RapidAPI by Fantastic.Jobs) scraper.

YC-backed companies only. Free, refreshed twice daily.
"""

from __future__ import annotations

import time
from datetime import datetime

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://free-y-combinator-jobs-api.p.rapidapi.com/active-jb-7d"
_RAPIDAPI_HOST = "free-y-combinator-jobs-api.p.rapidapi.com"
_TIMEOUT = 30
_PAGE_SIZE = 100
_MAX_PAGES = 5
_RATE_LIMIT_S = 0.5


def _first_str(item: dict, *keys: str) -> str:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class YCJobsScraper:
    """Scraper for the YC Jobs RapidAPI endpoint."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("yc_jobs.no_api_key")
            return []

        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        jobs: list[dict] = []
        seen_ids: set[str] = set()

        # YC dataset is small enough that we just paginate without filters
        # (avoids over-filtering and 0-result responses).
        for page in range(_MAX_PAGES):
            offset = page * _PAGE_SIZE
            try:
                resp = requests.get(
                    _BASE_URL,
                    headers=headers,
                    params={"limit": _PAGE_SIZE, "offset": offset},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                payload = resp.json()
                items = (
                    payload
                    if isinstance(payload, list)
                    else payload.get("data") or payload.get("jobs") or []
                )
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
                log.error("yc_jobs.fetch_error", page=page, error=str(exc))
                break

        log.info("yc_jobs.fetch_complete", count=len(jobs))
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        try:
            title = _first_str(item, "title", "job_title", "role", "position")
            company = _first_str(item, "company_name", "company", "organization", "startup")
            url = _first_str(item, "url", "job_url", "apply_url")
            if not (title and company and url):
                return None

            posted_dt = _parse_iso(_first_str(item, "date_posted", "posted_at", "published_at"))

            return {
                "title": title,
                "company_name": company,
                "description": _first_str(item, "description", "description_text"),
                "url": url,
                "source": "Y Combinator Jobs",
                "original_language": "en",
                "published_at": posted_dt,
                "location_raw": _first_str(item, "location", "city") or None,
                "salary_min": None,
                "salary_max": None,
                "currency": None,
                "external_id": _first_str(item, "id", "job_id") or url,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("yc_jobs.normalize_failed", error=str(exc))
            return None
