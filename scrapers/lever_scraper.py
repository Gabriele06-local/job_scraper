"""Lever ATS scraper — no auth required. Parallel fetch via asyncio."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.lever.co/v0/postings/{slug}"
_CONCURRENCY = 10
_BATCH_DELAY = 0.5
_TIMEOUT = 10
_RETRY_ATTEMPTS = 3


class LeverScraper:
    def fetch(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        return asyncio.run(self._fetch_all(companies))

    async def _fetch_all(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(_CONCURRENCY)
        jobs: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for i in range(0, len(companies), _CONCURRENCY):
                batch = companies[i : i + _CONCURRENCY]
                tasks = [self._fetch_company(client, sem, c) for c in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in batch_results:
                    if isinstance(res, list):
                        jobs.extend(res)
                if i + _CONCURRENCY < len(companies):
                    await asyncio.sleep(_BATCH_DELAY)

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
            for attempt in range(_RETRY_ATTEMPTS):
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
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in (429,) or 500 <= status < 600:
                        if attempt < _RETRY_ATTEMPTS - 1:
                            wait = 2 ** attempt
                            log.warning(
                                "lever.retry",
                                company=name,
                                status=status,
                                attempt=attempt + 1,
                            )
                            await asyncio.sleep(wait)
                            continue
                    log.error("lever.fetch_error", company=name, error=str(exc)[:120])
                    return []
                except httpx.TimeoutException:
                    if attempt < _RETRY_ATTEMPTS - 1:
                        wait = 2 ** attempt
                        log.warning("lever.retry_timeout", company=name, attempt=attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    log.error("lever.fetch_timeout", company=name)
                    return []
                except Exception as exc:
                    log.error("lever.fetch_error", company=name, error=str(exc)[:120])
                    return []
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
