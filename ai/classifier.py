"""Groq client wrapper for single-call job classification.

Retry: tenacity exp backoff, max 3 attempts.
Rate limit: client-side token bucket (GROQ_RPM env).
Logging: tokens in/out, latency per call.
Cost: cumulative tracker emitted at run end.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import groq
import structlog
from pydantic import BaseModel, ValidationError, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from models.job import (
    EmploymentType,
    JobClassification,
    RemoteMode,
    RoleFamily,
    Seniority,
)

logger = structlog.get_logger(__name__)

# Groq llama-3.1-8b-instant pricing (USD per 1M tokens, 2025 rates)
_INPUT_PRICE_PER_1M: float = 0.05
_OUTPUT_PRICE_PER_1M: float = 0.08

# Strict JSON schema sent to Groq (response_format json_object + Pydantic guard)
_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skills": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "seniority": {
            "type": "string",
            "enum": ["junior", "mid", "senior", "lead", "principal", "unknown"],
        },
        "role_family": {
            "type": "string",
            "enum": [
                "frontend", "backend", "fullstack", "devops", "data",
                "ml", "mobile", "qa", "security", "design", "pm", "other",
            ],
        },
        "employment_type": {
            "type": "string",
            "enum": [
                "full_time", "part_time", "contract",
                "freelance", "internship", "unknown",
            ],
        },
        "remote_mode": {
            "type": "string",
            "enum": ["onsite", "hybrid", "remote", "unknown"],
        },
        "salary_min": {"type": ["integer", "null"]},
        "salary_max": {"type": ["integer", "null"]},
        "currency": {"type": ["string", "null"]},
        "languages_required": {"type": "array", "items": {"type": "string"}},
        "quality_flags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "skills", "seniority", "role_family", "employment_type",
        "remote_mode", "languages_required", "quality_flags", "confidence",
    ],
}

_SYSTEM_PROMPT = (
    "You are a technical recruiter classifier. "
    "Extract structured information from job offers. "
    "Return ONLY valid JSON matching the requested schema."
)


def _build_user_prompt(text: str) -> str:
    truncated = text[:4000]  # 4000 chars carries classification signal per SPEC 00 §5
    schema_str = json.dumps(_CLASSIFICATION_SCHEMA, indent=2)
    return (
        f"Analyze this job offer and return JSON matching this schema:\n"
        f"{schema_str}\n\n"
        f"Job offer:\n{truncated}"
    )


# ---------------------------------------------------------------------------
# Raw Groq output validator (Pydantic, defense in depth per D-01-15)
# ---------------------------------------------------------------------------


class _GroqOutput(BaseModel):
    """Validates raw Groq JSON before mapping to JobClassification.

    Invalid enum values are coerced to defaults (AI sometimes returns novel strings).
    """

    skills: list[str] = []
    category: str | None = None
    seniority: Seniority = Seniority.UNKNOWN
    role_family: RoleFamily = RoleFamily.OTHER
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    remote_mode: RemoteMode = RemoteMode.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    languages_required: list[str] = []
    quality_flags: list[str] = []
    confidence: float = 0.0

    @field_validator("seniority", mode="before")
    @classmethod
    def coerce_seniority(cls, v: object) -> object:
        if isinstance(v, str) and v not in {e.value for e in Seniority}:
            return Seniority.UNKNOWN.value
        return v

    @field_validator("role_family", mode="before")
    @classmethod
    def coerce_role_family(cls, v: object) -> object:
        if isinstance(v, str) and v not in {e.value for e in RoleFamily}:
            return RoleFamily.OTHER.value
        return v

    @field_validator("employment_type", mode="before")
    @classmethod
    def coerce_employment_type(cls, v: object) -> object:
        if isinstance(v, str) and v not in {e.value for e in EmploymentType}:
            return EmploymentType.UNKNOWN.value
        return v

    @field_validator("remote_mode", mode="before")
    @classmethod
    def coerce_remote_mode(cls, v: object) -> object:
        if isinstance(v, str) and v not in {e.value for e in RemoteMode}:
            return RemoteMode.UNKNOWN.value
        return v


# ---------------------------------------------------------------------------
# Rate limiter
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
# Cost tracker (cumulative per process)
# ---------------------------------------------------------------------------


class _CostTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tokens_in: int = 0
        self.tokens_out: int = 0

    def record(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out

    @property
    def cost_usd(self) -> float:
        return (
            self.tokens_in * _INPUT_PRICE_PER_1M / 1_000_000
            + self.tokens_out * _OUTPUT_PRICE_PER_1M / 1_000_000
        )

    def summary(self) -> dict[str, Any]:
        return {
            "groq_tokens_in": self.tokens_in,
            "groq_tokens_out": self.tokens_out,
            "groq_cost_usd": round(self.cost_usd, 6),
        }


cost_tracker = _CostTracker()


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class GroqClassifier:
    """Single-call Groq classifier with retry, rate limiting, and structured logging."""

    def __init__(self, client: groq.Groq | None = None) -> None:
        self._client = client or groq.Groq(api_key=settings.groq_api_key)
        self._rate_limiter = _RateLimiter(settings.groq_rpm)

    def classify_job(self, text: str) -> JobClassification:
        """Classify a job offer text.

        Args:
            text: Combined title + description (caller builds the string).

        Returns:
            JobClassification with fields populated from Groq response.

        Raises:
            groq.RateLimitError | groq.APITimeoutError | groq.InternalServerError:
                After 3 failed attempts (tenacity re-raises).
            ValidationError: If Groq returns malformed JSON after retries.
        """
        return self._call_with_retry(text)

    @retry(
        retry=retry_if_exception_type(
            (groq.RateLimitError, groq.APITimeoutError, groq.InternalServerError)
        ),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_with_retry(self, text: str) -> JobClassification:
        self._rate_limiter.acquire()

        t0 = time.monotonic()
        response = self._client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(text)},
            ],
            response_format={"type": "json_object"},
            max_tokens=settings.groq_max_tokens,
            temperature=settings.groq_temperature,
            timeout=settings.groq_timeout,
        )
        latency_ms = round((time.monotonic() - t0) * 1000)

        # Extract token usage (None-safe for test mocks)
        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

        cost_tracker.record(tokens_in, tokens_out)

        logger.info(
            "groq.call",
            model=settings.groq_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )

        raw_content = response.choices[0].message.content
        try:
            raw_dict = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.warning("groq.json_parse_error", error=str(e), content=raw_content[:200])
            raise

        try:
            parsed = _GroqOutput.model_validate(raw_dict)
        except ValidationError as e:
            logger.warning("groq.validation_error", error=str(e))
            raise

        # skills lexicon split is TODO in claude-06; all skills → technical_skills
        return JobClassification(
            technical_skills=parsed.skills,
            skills=[],
            category=parsed.category,
            role_family=parsed.role_family,
            seniority=parsed.seniority,
            employment_type=parsed.employment_type,
            remote_mode=parsed.remote_mode,
            salary_min=parsed.salary_min,
            salary_max=parsed.salary_max,
            currency=parsed.currency,
            languages_required=parsed.languages_required,
            quality_flags=parsed.quality_flags,
            ai_confidence=parsed.confidence,
            ai_model=settings.groq_model,
            ai_call_at=datetime.now(tz=timezone.utc),
        )
