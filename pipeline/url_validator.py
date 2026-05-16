"""Pre-pipeline URL validator (SDD §A.1).

Lightweight HEAD probe (with bounded GET fallback) that filters out obviously
dead URLs *before* the expensive Groq classification step. Reuses the existing
`httpx` dependency already used by `pipeline/expiration.py`.

Concurrency:
    * Global asyncio.Semaphore — caps overall in-flight probes.
    * Per-host asyncio.Semaphore (defaultdict) — caps probes against a single
      netloc so we don't overload a target.

Dead-URL signals (`is_valid=False`, skip Groq):
    * HTTP 404 / 410
    * Final redirected path in {/, /jobs, /careers, /search}
    * DNS failure / connection refused

Treated as transient (`is_valid=True`, let `expire` catch it later):
    * Timeouts, 5xx, network glitches

Env overrides honoured by the wiring layer (cli.py):
    URL_VALIDATOR_TIMEOUT_S
    URL_VALIDATOR_MAX_CONCURRENCY
    URL_VALIDATOR_PER_HOST_CONCURRENCY
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Redirect targets that strongly suggest the listing is gone.
_REDIRECT_DEAD_PATHS = frozenset({"", "/", "/jobs", "/careers", "/search", "/home"})

_DEFAULT_USER_AGENT = "DevBoardsURLValidator/1.0 (+https://devboards.io)"

# GET fallback only reads a tiny prefix to cover hosts that refuse HEAD.
_GET_FALLBACK_HEADERS = {"Range": "bytes=0-2048"}


@dataclass(frozen=True)
class URLValidationResult:
    """Outcome of a single pre-pipeline URL probe."""

    url: str
    is_valid: bool
    status_code: int | None
    reason: str | None  # "http_404"|"http_410"|"redirect_to_root"|"timeout"|"dns_error"|"ok"


class PrePipelineURLValidator:
    """Async HEAD-first URL validator with semaphore-bounded concurrency."""

    def __init__(
        self,
        *,
        timeout_s: float = 4.0,
        per_host_concurrency: int = 4,
        global_concurrency: int = 32,
        user_agent: str | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._per_host = per_host_concurrency
        self._global_sem = asyncio.Semaphore(global_concurrency)
        self._host_sems: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_host_concurrency)
        )
        self._user_agent = user_agent or _DEFAULT_USER_AGENT

    async def validate_many(self, urls: list[str]) -> dict[str, URLValidationResult]:
        """Probe a batch of URLs concurrently; returns {url: result}.

        Duplicates in the input list are collapsed (one probe per unique URL).
        """
        unique = list(dict.fromkeys(u for u in urls if u))
        if not unique:
            return {}

        headers = {"User-Agent": self._user_agent}
        limits = httpx.Limits(
            max_connections=self._global_sem._value,  # noqa: SLF001 — public API absent
            max_keepalive_connections=max(2, self._global_sem._value // 2),  # noqa: SLF001
        )

        async with httpx.AsyncClient(
            http2=False,  # http2 is optional; off by default to avoid hard dep
            follow_redirects=True,
            timeout=self._timeout_s,
            headers=headers,
            limits=limits,
        ) as client:
            tasks = [self._validate_one(u, client) for u in unique]
            results = await asyncio.gather(*tasks, return_exceptions=False)

        out: dict[str, URLValidationResult] = {r.url: r for r in results}
        invalid = sum(1 for r in results if not r.is_valid)
        logger.info(
            "url_validator.batch_done",
            total=len(results),
            invalid=invalid,
            valid=len(results) - invalid,
        )
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _validate_one(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> URLValidationResult:
        host = urlparse(url).hostname or url
        host_sem = self._host_sems[host]

        try:
            async with self._global_sem, host_sem:
                return await self._probe(url, client)
        except Exception as exc:  # defensive: semaphore release happens via async with
            logger.warning("url_validator.unexpected_error", url=url, error=str(exc)[:200])
            # Fail-open: transient → let downstream `expire` catch it.
            return URLValidationResult(url=url, is_valid=True, status_code=None, reason="error")

    async def _probe(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> URLValidationResult:
        try:
            resp = await client.head(url)
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            # DNS / connection refused — treat as dead.
            return URLValidationResult(
                url=url,
                is_valid=False,
                status_code=None,
                reason="dns_error" if _looks_like_dns(exc) else "connection_refused",
            )
        except httpx.TimeoutException:
            # Transient — keep alive, let `expire` deep-scan later.
            return URLValidationResult(url=url, is_valid=True, status_code=None, reason="timeout")
        except httpx.HTTPError as exc:
            logger.debug("url_validator.head_error", url=url, error=str(exc)[:200])
            return URLValidationResult(url=url, is_valid=True, status_code=None, reason="error")

        # Some hosts return 405/501 to HEAD — fall back to bounded GET.
        if resp.status_code in (405, 501):
            try:
                resp = await client.get(url, headers=_GET_FALLBACK_HEADERS)
            except httpx.TimeoutException:
                return URLValidationResult(
                    url=url, is_valid=True, status_code=None, reason="timeout"
                )
            except httpx.HTTPError as exc:
                logger.debug("url_validator.get_fallback_error", url=url, error=str(exc)[:200])
                return URLValidationResult(
                    url=url, is_valid=True, status_code=None, reason="error"
                )

        return _classify_response(url, resp)


def _looks_like_dns(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "name or service" in msg or "nodename nor servname" in msg or "getaddrinfo" in msg


def _classify_response(
    requested_url: str,
    resp: httpx.Response,
) -> URLValidationResult:
    code = resp.status_code

    if code == 404:
        return URLValidationResult(
            url=requested_url, is_valid=False, status_code=404, reason="http_404"
        )
    if code == 410:
        return URLValidationResult(
            url=requested_url, is_valid=False, status_code=410, reason="http_410"
        )

    # Detect a redirect chain that landed on a generic listing root.
    final_path = (urlparse(str(resp.url)).path or "/").rstrip("/") or "/"
    if resp.history and final_path in _REDIRECT_DEAD_PATHS:
        return URLValidationResult(
            url=requested_url,
            is_valid=False,
            status_code=code,
            reason="redirect_to_root",
        )

    # 2xx + 3xx (without dead-end redirect) — alive.
    if 200 <= code < 400:
        return URLValidationResult(
            url=requested_url, is_valid=True, status_code=code, reason="ok"
        )

    # 4xx other than 404/410 — let it through; downstream will re-evaluate.
    # 5xx — transient.
    return URLValidationResult(
        url=requested_url, is_valid=True, status_code=code, reason="transient"
    )


# Allow callers to use `from pipeline.url_validator import _classify_response` for tests.
__all__ = [
    "URLValidationResult",
    "PrePipelineURLValidator",
]


def build_default() -> PrePipelineURLValidator:
    """Factory honouring env overrides; called from cli wiring."""
    import os

    timeout = float(os.environ.get("URL_VALIDATOR_TIMEOUT_S", "4.0"))
    global_conc = int(os.environ.get("URL_VALIDATOR_MAX_CONCURRENCY", "32"))
    per_host = int(os.environ.get("URL_VALIDATOR_PER_HOST_CONCURRENCY", "4"))
    return PrePipelineURLValidator(
        timeout_s=timeout,
        per_host_concurrency=per_host,
        global_concurrency=global_conc,
    )


# Re-export helper as module-private (used by cli wiring + tests).
_classify_response_for_tests: Optional[object] = _classify_response
