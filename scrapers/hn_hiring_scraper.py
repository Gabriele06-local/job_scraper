"""HN 'Who is Hiring' API (RapidAPI by OdosUI) scraper.

The API parses the monthly HN 'Who is Hiring' thread, extracting
structured fields from each comment. One comment can advertise multiple
positions (`extracted.jobs[]`), so we fan out a separate RawJob per role.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://hacker-news-who-is-hiring-api.p.rapidapi.com/jobs"
_RAPIDAPI_HOST = "hacker-news-who-is-hiring-api.p.rapidapi.com"
_TIMEOUT = 30
_PER_PAGE = 100
_MAX_PAGES = 10  # /jobs reports totalPages in payload; this is a safety cap
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


def _epoch_to_dt(value: object) -> datetime | None:
    try:
        if value in (None, "", False):
            return None
        return datetime.fromtimestamp(int(value), tz=timezone.utc)  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError):
        return None


def _join_location(locations: object) -> str:
    """Join the first location dict into 'city, country' format."""
    if not isinstance(locations, list) or not locations:
        return ""
    first = locations[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        parts = [str(first.get(k, "")) for k in ("city", "country")]
        return ", ".join(p for p in parts if p)
    return ""


class HNHiringScraper:
    """Scraper for the HN 'Who is Hiring' RapidAPI endpoint."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def fetch(self) -> list[dict]:
        if not self._api_key:
            log.warning("hn_hiring.no_api_key")
            return []

        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        jobs: list[dict] = []
        seen_ids: set[str] = set()
        total_pages = _MAX_PAGES

        for page in range(1, _MAX_PAGES + 1):
            if page > total_pages:
                break
            try:
                resp = requests.get(
                    _BASE_URL,
                    headers=headers,
                    params={"page": page, "perPage": _PER_PAGE},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                payload = resp.json()
                # Update total_pages once we know it.
                if isinstance(payload, dict):
                    reported = payload.get("totalPages")
                    if isinstance(reported, int) and reported > 0:
                        total_pages = min(reported, _MAX_PAGES)
                items = (
                    (payload.get("items") or payload.get("jobs") or payload.get("data") or [])
                    if isinstance(payload, dict)
                    else (payload if isinstance(payload, list) else [])
                )
                if not items:
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    for normalized in self._fan_out(item, seen_ids):
                        jobs.append(normalized)
                time.sleep(_RATE_LIMIT_S)
            except requests.RequestException as exc:
                log.error("hn_hiring.fetch_error", page=page, error=str(exc))
                break

        log.info("hn_hiring.fetch_complete", count=len(jobs))
        return jobs

    def _fan_out(self, item: dict, seen_ids: set[str]) -> list[dict]:
        """Yield one normalized RawJob per role inside a single HN comment.

        The API packs multiple roles into `extracted.jobs[]` for a single
        commentId. Each role gets a unique external_id `<commentId>-<idx>`.
        """
        try:
            comment_id = str(item.get("commentId") or "")
            comment_url = _first_str(item, "commentUrl", "url")
            extracted = item.get("extracted") or {}
            if not isinstance(extracted, dict):
                return []

            company = _first_str(extracted, "company")
            if not (company and comment_url):
                return []

            roles = extracted.get("jobs") or []
            if not isinstance(roles, list) or not roles:
                return []

            location = _join_location(extracted.get("locations"))
            posted_dt = _parse_iso(_first_str(item, "createdAt", "postedAt")) or _epoch_to_dt(
                item.get("time")
            )
            keywords_summary = ""

            results: list[dict] = []
            for idx, role in enumerate(roles):
                if not isinstance(role, dict):
                    continue
                title = _first_str(role, "role", "title")
                if not title:
                    continue
                url = _first_str(role, "url") or comment_url
                eid = f"{comment_id}-{idx}" if comment_id else url
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)

                keywords = role.get("keywords") or []
                if isinstance(keywords, list) and keywords:
                    keywords_summary = "Keywords: " + ", ".join(str(k) for k in keywords if k)

                results.append(
                    {
                        "title": title,
                        "company_name": company,
                        "description": keywords_summary,
                        "url": url,
                        "source": "HN Who is Hiring",
                        "original_language": "en",
                        "published_at": posted_dt,
                        "location_raw": location or None,
                        "salary_min": extracted.get("salaryFrom"),
                        "salary_max": extracted.get("salaryTo"),
                        "currency": None,
                        "external_id": eid,
                    }
                )
            return results
        except Exception as exc:  # noqa: BLE001 — never crash the pipeline
            log.warning("hn_hiring.normalize_failed", error=str(exc))
            return []
