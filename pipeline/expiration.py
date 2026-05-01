"""Expiration checker — SPEC 04 §3.

Probes active job URLs with HTTP HEAD (GET fallback) and marks dead ones expired.
Async with semaphore (max 10) + per-domain rate limiting (1 req/sec).
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import structlog
from pymongo.collection import Collection

from config import settings
from pipeline.expiration_patterns import (
    EXPIRED_REDIRECT_PATTERNS,
    EXPIRY_BODY_PATTERNS,
    GENERIC_EXPIRY_PATTERNS,
)

log = structlog.get_logger(__name__)

_EXPIRED_STATUS_CODES = {404, 410, 451}
_TRANSIENT_STATUS_CODES = range(500, 600)


@dataclass
class ExpirationVerdict:
    """Result of a single URL probe."""

    expired: bool | None  # None = transient (retry tomorrow)
    reason: str = ""


@dataclass
class ExpirationCounters:
    total: int = 0
    probed: int = 0
    expired: int = 0
    transient: int = 0
    alive: int = 0
    error: int = 0
    max_age_expired: int = 0
    dry_run_skipped: int = 0


@dataclass
class ExpirationResult:
    counters: ExpirationCounters = field(default_factory=ExpirationCounters)
    errors: list[str] = field(default_factory=list)


def _redirect_is_expired(location: str) -> bool:
    return any(p.search(location) for p in EXPIRED_REDIRECT_PATTERNS)


def _body_matches_expiry(source: str, body: str) -> bool:
    patterns = EXPIRY_BODY_PATTERNS.get(source)
    if patterns:
        return any(p.search(body) for p in patterns)
    # Fallback: check generic patterns only on short bodies to avoid false positives
    if len(body) < 500:
        return any(p.search(body) for p in GENERIC_EXPIRY_PATTERNS)
    return False


class _DomainLimiter:
    """Per-domain asyncio lock + last-request timestamp for 1 req/sec throttle."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    def _lock(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    async def acquire(self, host: str) -> None:
        async with self._lock(host):
            now = asyncio.get_event_loop().time()
            last = self._last.get(host, 0.0)
            wait = 1.0 - (now - last) + random.uniform(0, 0.2)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = asyncio.get_event_loop().time()


class ExpirationChecker:
    """Probes active job URLs and marks expired ones in MongoDB.

    Args:
        jobs_col: MongoDB jobs collection.
        dry_run: Skip all writes when True.
    """

    def __init__(self, jobs_col: Collection, dry_run: bool = False) -> None:  # type: ignore[type-arg]
        self._jobs_col = jobs_col
        self._dry_run = dry_run
        self._limiter = _DomainLimiter()
        self._sem = asyncio.Semaphore(settings.expiration_concurrency)

    async def run(
        self,
        limit: int | None = None,
        max_age_days: int | None = None,
    ) -> ExpirationResult:
        """Probe active jobs and update expiry status."""
        result = ExpirationResult()
        c = result.counters
        effective_limit = limit or settings.expiration_limit
        effective_max_age = max_age_days or settings.expiration_max_age_days
        now = datetime.now(tz=timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_age = now - timedelta(days=effective_max_age)

        # 1. Mark max-age jobs expired without probing
        old_filter = {
            "status": {"$in": ["valid", "premium"]},
            "posted_at": {"$lt": cutoff_age},
            "$or": [
                {"last_probed_at": None},
                {"last_probed_at": {"$exists": False}},
            ],
        }
        if not self._dry_run:
            old_result = self._jobs_col.update_many(
                old_filter,
                {
                    "$set": {
                        "status": "expired",
                        "reject_reason": "EXPIRED_MAX_AGE",
                        "expires_at": now,
                        "last_probed_at": now,
                        "updated_at": now,
                    }
                },
            )
            c.max_age_expired = old_result.modified_count
        else:
            c.max_age_expired = self._jobs_col.count_documents(old_filter)

        # 2. Pick jobs due for probing
        probe_filter = {
            "status": {"$in": ["valid", "premium"]},
            "$or": [
                {"last_probed_at": None},
                {"last_probed_at": {"$exists": False}},
                {"last_probed_at": {"$lt": cutoff_24h}},
            ],
        }
        docs = list(
            self._jobs_col.find(probe_filter)
            .sort([("last_probed_at", 1)])
            .limit(effective_limit)
        )
        c.total = len(docs)

        if not docs:
            log.info("expiration.no_candidates", max_age_expired=c.max_age_expired)
            return result

        log.info("expiration.start", candidates=c.total, max_age_expired=c.max_age_expired)

        headers = {"User-Agent": settings.expiration_user_agent}
        limits = httpx.Limits(
            max_connections=settings.expiration_concurrency,
            max_keepalive_connections=settings.expiration_concurrency // 2,
        )

        async with httpx.AsyncClient(
            headers=headers,
            limits=limits,
            follow_redirects=False,
            timeout=10.0,
        ) as client:
            tasks = [self._probe_and_update(doc, client, c, result, now) for doc in docs]
            await asyncio.gather(*tasks)

        log.info(
            "expiration.complete",
            total=c.total,
            probed=c.probed,
            expired=c.expired,
            transient=c.transient,
            alive=c.alive,
            error=c.error,
            max_age_expired=c.max_age_expired,
            dry_run=self._dry_run,
        )
        return result

    async def _probe_and_update(
        self,
        doc: dict,  # type: ignore[type-arg]
        client: httpx.AsyncClient,
        c: ExpirationCounters,
        result: ExpirationResult,
        now: datetime,
    ) -> None:
        url: str = doc.get("url") or doc.get("link", "")
        source: str = doc.get("source", "")
        doc_id = doc.get("_id")

        if not url:
            c.error += 1
            return

        try:
            async with self._sem:
                host = urlparse(url).hostname or url
                await self._limiter.acquire(host)
                verdict = await self._probe(url, source, client)
        except Exception as exc:
            log.warning("expiration.probe_error", url=url, error=str(exc))
            result.errors.append(f"{url}: {exc}")
            c.error += 1
            verdict = ExpirationVerdict(expired=None, reason="PROBE_ERROR")

        c.probed += 1
        if verdict.expired is True:
            c.expired += 1
        elif verdict.expired is None:
            c.transient += 1
        else:
            c.alive += 1

        if self._dry_run:
            c.dry_run_skipped += 1
            return

        update: dict = {"last_probed_at": now, "updated_at": now}  # type: ignore[type-arg]
        if verdict.expired is True:
            update.update(
                {
                    "status": "expired",
                    "reject_reason": verdict.reason,
                    "expires_at": now,
                }
            )

        self._jobs_col.update_one({"_id": doc_id}, {"$set": update})

    async def _probe(
        self, url: str, source: str, client: httpx.AsyncClient
    ) -> ExpirationVerdict:
        try:
            r = await client.head(url)
        except httpx.TooManyRedirects:
            return ExpirationVerdict(expired=True, reason="EXPIRED_REDIRECT_LOOP")
        except httpx.HTTPError:
            return ExpirationVerdict(expired=None, reason="PROBE_ERROR")

        code = r.status_code

        if code in _EXPIRED_STATUS_CODES:
            return ExpirationVerdict(expired=True, reason=f"EXPIRED_{code}")

        if code in (301, 302, 303, 307, 308):
            location = r.headers.get("location", "")
            if _redirect_is_expired(location):
                return ExpirationVerdict(expired=True, reason="EXPIRED_REDIRECT")
            return ExpirationVerdict(expired=False)

        if code in _TRANSIENT_STATUS_CODES:
            return ExpirationVerdict(expired=None, reason=f"TRANSIENT_{code}")

        if code == 200:
            if source in EXPIRY_BODY_PATTERNS:
                try:
                    get_r = await client.get(url)
                    body = get_r.text
                except httpx.HTTPError:
                    return ExpirationVerdict(expired=False)
                if _body_matches_expiry(source, body):
                    return ExpirationVerdict(expired=True, reason="EXPIRED_PATTERN")
            return ExpirationVerdict(expired=False)

        return ExpirationVerdict(expired=False)
