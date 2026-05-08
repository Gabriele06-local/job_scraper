"""Lever ATS scraper — no auth required. Parallel fetch via asyncio."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.lever.co/v0/postings/{slug}"
_CONCURRENCY = 10
_TIMEOUT = 10


class LeverScraper:
    def fetch(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        return asyncio.run(self._fetch_all(companies))

    async def _fetch_all(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(_CONCURRENCY)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            tasks = [self._fetch_company(client, sem, c) for c in companies]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        jobs: list[dict[str, Any]] = []
        for res in results:
            if isinstance(res, list):
                jobs.extend(res)
        return jobs

    async def _fetch_company(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        company: dict[str, str],
    ) -> list[dict[str, Any]]:
        slug = company["slug"]
        name = company["name"]
        async with sem:
            try:
                resp = await client.get(
                    _BASE_URL.format(slug=slug),
                    params={"mode": "json"},
                )
                resp.raise_for_status()
                raw = resp.json()
                if isinstance(raw, list):
                    log.debug("lever.fetched", company=name, count=len(raw))
                    return [j for item in raw if (j := self._normalize(item, name))]
            except (httpx.HTTPError, Exception) as exc:
                log.error("lever.fetch_error", company=name, error=str(exc)[:120])
            return []

    def _normalize(self, item: dict[str, Any], company_name: str) -> dict[str, Any] | None:
        title = item.get("text") or ""
        url = item.get("hostedUrl") or ""
        if not (title and url):
            return None
        cats = item.get("categories") or {}
        location = cats.get("location") or cats.get("team") or ""
        sal = item.get("salaryRange") or {}
        return {
            "title": title,
            "company_name": company_name,
            "description": item.get("descriptionPlain") or item.get("description") or "",
            "url": url,
            "source": "Lever",
            "original_language": "en",
            "published_at": item.get("createdAt"),
            "location_raw": location,
            "salary_min": sal.get("min"),
            "salary_max": sal.get("max"),
            "currency": sal.get("currency"),
            "external_id": str(item.get("id", "")),
        }
