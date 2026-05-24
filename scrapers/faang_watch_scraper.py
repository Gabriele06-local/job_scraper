"""faang.watch API (RapidAPI by Local Transformer) scraper.

Unified FAANG career pages: Google, Meta, Apple, Amazon, Netflix.
All roles are tech-flavored.
"""

from __future__ import annotations

import time
from datetime import datetime

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://faang-watch-api.p.rapidapi.com/seniority"
_RAPIDAPI_HOST = "faang-watch-api.p.rapidapi.com"
_TIMEOUT = 30
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


class FaangWatchScraper:
    """Scraper for the faang.watch RapidAPI endpoint."""

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

        # The /seniority endpoint returns the full pool grouped by
        # seniority bucket. We flatten across buckets in one request.
        try:
            resp = requests.get(_BASE_URL, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            items = self._flatten(payload)
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
            log.error("faang_watch.fetch_error", error=str(exc))

        log.info("faang_watch.fetch_complete", count=len(jobs))
        return jobs

    @staticmethod
    def _flatten(payload: object) -> list:
        """Pull job-like dicts out of an opaque payload shape.

        Tries flat lists, common wrapper keys, and "bucket" dicts where
        each value is a list of jobs (e.g. {senior:[...], mid:[...]}).
        """
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("jobs", "data", "items", "results"):
                v = payload.get(key)
                if isinstance(v, list):
                    return v
            # Bucket shape: {bucket_name: [job, ...], ...}
            flat: list = []
            for v in payload.values():
                if isinstance(v, list):
                    flat.extend(v)
            if flat:
                return flat
        return []

    def _normalize(self, item: dict, fallback_company: str = "") -> dict | None:
        try:
            title = _first_str(item, "title", "job_title", "role")
            company = (
                _first_str(item, "company", "company_name", "employer")
                or fallback_company.title()
            )
            url = _first_str(item, "url", "job_url", "apply_url", "link")
            if not (title and company and url):
                return None

            posted_dt = _parse_iso(
                _first_str(item, "date_posted", "posted_at", "published_at")
            )

            return {
                "title": title,
                "company_name": company,
                "description": _first_str(item, "description", "summary"),
                "url": url,
                "source": "faang.watch",
                "original_language": "en",
                "published_at": posted_dt,
                "location_raw": _first_str(item, "location", "city") or None,
                "salary_min": None,
                "salary_max": None,
                "currency": None,
                "external_id": _first_str(item, "id", "job_id") or url,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("faang_watch.normalize_failed", error=str(exc))
            return None
