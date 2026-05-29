"""HN Real-Time Jobs API (RapidAPI by Syed Abdulla) scraper.

Real-time Hacker News job postings with company/location extraction.
Complements hn_hiring (monthly thread) with continuous data.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://hacker-news-real-time-jobs-startup-hiring-api.p.rapidapi.com/jobs/search"
_RAPIDAPI_HOST = "hacker-news-real-time-jobs-startup-hiring-api.p.rapidapi.com"
_TIMEOUT = 30
_RATE_LIMIT_S = 0.5
_KEYWORDS = [
    "engineer",
    "developer",
    "python",
    "rust",
    "go",
    "typescript",
    "data",
    "senior",
]


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


def _epoch_to_dt(value: object) -> datetime | None:
    try:
        if value in (None, "", False):
            return None
        return datetime.fromtimestamp(int(value), tz=timezone.utc)  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError):
        return None


class HNRealtimeScraper:
    """Scraper for the HN Real-Time Jobs RapidAPI endpoint."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("hn_realtime.no_api_key")
            return []

        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        jobs: list[dict] = []
        seen_ids: set[str] = set()

        # /jobs/search requires a `q` query param. No documented
        # pagination — we sweep a small set of broad keywords and union
        # their results. The endpoint is idempotent per query, so dedup
        # across keywords via seen_ids.
        from utils.retry import requests_retry

        @requests_retry
        def _fetch_keyword(keyword: str) -> list | dict:
            resp = requests.get(
                _BASE_URL,
                headers=headers,
                params={"q": keyword},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()

        for keyword in _KEYWORDS:
            try:
                payload = _fetch_keyword(keyword)
                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    items = (
                        payload.get("jobs")
                        or payload.get("data")
                        or payload.get("results")
                        or payload.get("items")
                        or []
                    )
                else:
                    items = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    eid = _first_str(item, "id", "hn_id", "url")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)
                    normalized = self._normalize(item)
                    if normalized:
                        jobs.append(normalized)
                time.sleep(_RATE_LIMIT_S)
            except requests.RequestException as exc:
                log.error("hn_realtime.fetch_error", keyword=keyword, error=str(exc))
                continue

        log.info("hn_realtime.fetch_complete", count=len(jobs))
        return jobs

    def _normalize(self, item: dict) -> dict | None:
        try:
            title = _first_str(item, "title", "role", "position", "job_title")
            company = _first_str(item, "company", "company_name", "organization")
            url = _first_str(item, "url", "apply_url", "hn_url", "link")
            description = _first_str(item, "description", "text", "body")

            if not (title and company and url):
                return None

            posted_dt = _parse_iso(
                _first_str(
                    item,
                    "posted_date",
                    "date",
                    "posted_at",
                    "published_at",
                    "created_at",
                    "scraped_at",
                )
            ) or _epoch_to_dt(item.get("time") or item.get("timestamp"))

            return {
                "title": title,
                "company_name": company,
                "description": description,
                "url": url,
                "source": "HN Real-Time Jobs",
                "original_language": "en",
                "published_at": posted_dt,
                "location_raw": _first_str(item, "location", "city") or None,
                "salary_min": None,
                "salary_max": None,
                "currency": None,
                "external_id": _first_str(item, "id", "hn_id") or url,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("hn_realtime.normalize_failed", error=str(exc))
            return None
