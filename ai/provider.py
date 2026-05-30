"""Provider-agnostic LLM interface (SPEC 05 §4.1).

This is the **only** module in the codebase that imports the ``groq`` SDK or
knows how to talk to the Groq HTTP API. Everything above it (router, classifier,
tasks) references the :class:`LLMProvider` protocol and capability-level tasks,
never a concrete client or model literal (SPEC 05 AC-1, constraint C-4).

The provider performs **one** round-trip and does **no** retry or escalation —
those are the router's responsibility (SPEC 05 §4.2). It does own the
client-side rate limit (``GROQ_RPM``) and translates SDK-specific exceptions
into provider-agnostic :class:`LLMTransientError` / :class:`LLMError` so callers
stay decoupled from the SDK.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

import groq
import structlog

from config import settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors (provider-agnostic — callers must not import groq exceptions)
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for provider failures."""


class LLMTransientError(LLMError):
    """Retriable failure (rate limit, timeout, upstream 5xx)."""


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Raw text completion plus usage metadata for telemetry."""

    content: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


# ---------------------------------------------------------------------------
# Rate limiter (moved verbatim from ai/classifier.py)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Client-side token bucket — enforces GROQ_RPM."""

    def __init__(self, rpm: int) -> None:
        self._min_interval = 60.0 / max(rpm, 1)
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# Provider protocol + Groq implementation
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Capability a router needs: one JSON completion for a given model."""

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> LLMResponse: ...


class GroqProvider:
    """Groq-backed :class:`LLMProvider`. The sole Groq SDK touch-point."""

    def __init__(
        self, client: groq.Groq | None = None, rpm: int | None = None
    ) -> None:
        self._client = client or groq.Groq(api_key=settings.groq_api_key)
        self._rate_limiter = _RateLimiter(rpm if rpm is not None else settings.groq_rpm)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> LLMResponse:
        """One JSON-mode completion. Raises :class:`LLMTransientError` on
        rate-limit/timeout/5xx so the router can retry or escalate; any other
        SDK error becomes :class:`LLMError`.
        """
        self._rate_limiter.acquire()
        t0 = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        except (
            groq.RateLimitError,
            groq.APITimeoutError,
            groq.InternalServerError,
        ) as exc:
            raise LLMTransientError(str(exc)) from exc
        except groq.GroqError as exc:  # any other SDK error — non-transient
            raise LLMError(str(exc)) from exc

        latency_ms = round((time.monotonic() - t0) * 1000)
        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        content = response.choices[0].message.content or ""
        return LLMResponse(
            content=content,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
