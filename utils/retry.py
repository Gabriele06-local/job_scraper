"""Shared retry utilities for HTTP requests."""

from __future__ import annotations

import logging

import httpx
import requests
import structlog
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 10.0


def _is_retryable_request_error(exc: BaseException) -> bool:
    if isinstance(exc, requests.RequestException):
        if exc.response is not None:
            status = exc.response.status_code
            return status in (429,) or 500 <= status < 600
        return True
    return False


def _is_retryable_httpx_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in (429,) or 500 <= status < 600
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    return False


requests_retry = retry(
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=_MIN_WAIT, max=_MAX_WAIT),
    retry=_is_retryable_request_error,
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)

httpx_retry = retry(
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=_MIN_WAIT, max=_MAX_WAIT),
    retry=_is_retryable_httpx_error,
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)


@requests_retry
def safe_get(url: str, **kwargs: object) -> requests.Response:
    """Wrapper around requests.get with exponential backoff retry."""
    return requests.get(url, **kwargs)


@requests_retry
def safe_post(url: str, **kwargs: object) -> requests.Response:
    """Wrapper around requests.post with exponential backoff retry."""
    return requests.post(url, **kwargs)
