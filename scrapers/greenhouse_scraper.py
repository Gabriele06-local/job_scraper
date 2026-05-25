"""Greenhouse ATS scraper — no auth required.

Parallel fetching with asyncio semaphore (10 concurrent).
500ms delay between company batches.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.greenhouse.io/v1/boards/{slug}/jobs"
_CONCURRENCY = 10
_BATCH_DELAY = 0.5
_TIMEOUT = 10
_RETRY_ATTEMPTS = 3


class GreenhouseScraper:
    def fetch(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        return asyncio.run(self._fetch_all(companies))

    async def _fetch_all(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(_CONCURRENCY)
        results: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            batch_size = _CONCURRENCY
            for i in range(0, len(companies), batch_size):
                batch = companies[i : i + batch_size]
                tasks = [self._fetch_company(client, sem, c) for c in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in batch_results:
                    if isinstance(res, list):
                        results.extend(res)
                await asyncio.sleep(_BATCH_DELAY)

        return results

    async def _fetch_company(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        company: dict[str, str],
    ) -> list[dict[str, Any]]:
        slug = company["slug"]
        name = company["name"]
        url = _BASE_URL.format(slug=slug)

        async with sem:
            for attempt in range(_RETRY_ATTEMPTS):
                try:
                    resp = await client.get(url, params={"content": "true"})
                    resp.raise_for_status()
                    data = resp.json()
                    jobs_raw = data.get("jobs", [])
                    log.debug("greenhouse.fetched", company=name, count=len(jobs_raw))
                    return [self._normalize(j, name) for j in jobs_raw if self._normalize(j, name)]
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in (429,) or 500 <= status < 600:
                        if attempt < _RETRY_ATTEMPTS - 1:
                            wait = 2 ** attempt
                            log.warning(
                                "greenhouse.retry",
                                company=name,
                                status=status,
                                attempt=attempt + 1,
                            )
                            await asyncio.sleep(wait)
                            continue
                    log.error(
                        "greenhouse.fetch_error",
                        company=name,
                        error=str(exc)[:120],
                    )
                    return []
                except httpx.TimeoutException:
                    if attempt < _RETRY_ATTEMPTS - 1:
                        wait = 2 ** attempt
                        log.warning("greenhouse.retry_timeout", company=name, attempt=attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    log.error("greenhouse.fetch_timeout", company=name)
                    return []
                except Exception as exc:
                    log.error("greenhouse.fetch_error", company=name, error=str(exc)[:120])
                    return []
            return []

    def _normalize(self, item: dict[str, Any], company_name: str) -> dict[str, Any] | None:
        title = item.get("title") or ""
        url = item.get("absolute_url") or ""
        if not (title and url):
            return None
        offices = item.get("offices", [])
        location = offices[0].get("name") if offices else item.get("location", {}).get("name", "")
        return {
            "title": title,
            "company_name": company_name,
            "description": item.get("content") or "",
            "url": url,
            "source": "Greenhouse",
            "original_language": "en",
            "published_at": item.get("updated_at"),
            "location_raw": location,
            "salary_min": None,
            "salary_max": None,
            "external_id": str(item.get("id", "")),
        }
