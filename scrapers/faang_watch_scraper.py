"""faang.watch API (RapidAPI by Local Transformer) scraper.

Unified FAANG career pages: Google, Meta, Apple, Amazon, Netflix.
Endpoint /search returns batches of normalized postings with parsed
locations, categories, seniority and ISO dates.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://faang-watch-api.p.rapidapi.com/search"
_RAPIDAPI_HOST = "faang-watch-api.p.rapidapi.com"
_TIMEOUT = 30
_PAGE_SIZE = 100
_MAX_PAGES = 5
_RATE_LIMIT_S = 0.5
_COMPANIES = ["Amazon", "Google", "Meta", "Apple", "Netflix"]
# Restrict to dev-focused categories; the API accepts a JSON array string.
_CATEGORIES_JSON = json.dumps(["Software Engineering", "Cloud Engineering", "Systems Engineering"])
_FRESHNESS = "month"


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


def _join_location(item: dict) -> str:
    """Prefer parsed_locations[0] {city,state,country}, fall back to locations[0]."""
    parsed = item.get("parsed_locations")
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict):
            parts = [str(first.get(k, "")) for k in ("city", "state", "country") if first.get(k)]
            joined = ", ".join(p for p in parts if p)
            if joined:
                return joined
    locs = item.get("locations")
    if isinstance(locs, list) and locs and isinstance(locs[0], str):
        return locs[0]
    return ""


class FaangWatchScraper:
    """Scraper for the faang.watch RapidAPI /search endpoint."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("faang_watch.no_api_key")
            return []

        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        jobs: list[dict] = []
        seen_ids: set[str] = set()

        from utils.retry import requests_retry

        @requests_retry
        def _fetch_page(company: str, page: int) -> dict:
            resp = requests.get(
                _BASE_URL,
                headers=headers,
                params={
                    "company": company,
                    "categories": _CATEGORIES_JSON,
                    "freshness": _FRESHNESS,
                    "offset": page * _PAGE_SIZE,
                    "page_size": _PAGE_SIZE,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()

        for company in _COMPANIES:
            for page in range(_MAX_PAGES):
                try:
                    payload = _fetch_page(company, page)
                    items = (payload.get("batch") if isinstance(payload, dict) else None) or (
                        payload if isinstance(payload, list) else []
                    )
                    if not items:
                        break
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        eid = _first_str(item, "job_id", "id") or _first_str(item, "company_url")
                        if not eid or eid in seen_ids:
                            continue
                        seen_ids.add(eid)
                        normalized = self._normalize(item)
                        if normalized:
                            jobs.append(normalized)
                    time.sleep(_RATE_LIMIT_S)
                except requests.RequestException as exc:
                    log.error(
                        "faang_watch.fetch_error",
                        company=company,
                        page=page,
                        error=str(exc),
                    )
                    break

        log.info("faang_watch.fetch_complete", count=len(jobs))
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        try:
            title = _first_str(item, "title", "job_title", "role")
            company = _first_str(item, "company", "company_name", "employer")
            # `company_url` is the apply link to the specific posting,
            # not the company website.
            url = _first_str(item, "company_url", "url", "apply_url", "job_url")
            if not (title and company and url):
                return None

            posted_dt = _parse_iso(_first_str(item, "earliest_date", "date_posted", "posted_at"))

            return {
                "title": title,
                "company_name": company,
                "description": _first_str(item, "description", "summary"),
                "url": url,
                "source": "faang.watch",
                "original_language": "en",
                "published_at": posted_dt,
                "location_raw": _join_location(item) or None,
                "salary_min": None,
                "salary_max": None,
                "currency": None,
                "external_id": _first_str(item, "job_id", "id") or url,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("faang_watch.normalize_failed", error=str(exc))
            return None
