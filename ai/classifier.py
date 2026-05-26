"""Groq client wrapper for single-call job classification.

Retry semantics (three nested layers — keep them in mind when debugging):

1. **API-level retry** (`classify` loop): 3 attempts with exponential backoff +
   jitter on `RateLimitError`, `APITimeoutError`, `InternalServerError`. JSON
   parse / Pydantic validation errors trigger a corrective-hint re-prompt
   (the next attempt prepends "Your previous response was invalid. Error: ...").

2. **Confidence-level retry** (SDD §A.7): when the first successful call
   returns `ai_confidence < _DEFAULT_CONFIDENCE_THRESHOLD` and we have not
   already retried, we issue a second call with a `retry_with_hints` variant
   that explicitly tells the model:

       "If a field is genuinely unknowable from the text, return the literal
        string 'unknown'. Do not invent values to raise confidence."

   The retried result is returned even when its confidence is still low — the
   downstream `quality_gate` is the authority on rejection (`<0.7` reject,
   `>=0.85` premium-eligible).

3. **Tenacity decorator** (`_call_with_retry`): legacy free-form interface
   used by `classify_job(text)`. Same API-retry policy as (1).

Prompt structure (SPEC 02 §4):
  * System prompt is constant and cache-friendly — DO NOT mutate it per
    request, otherwise Groq's prompt cache won't kick in.
  * User prompt has two variants:
      `_build_structured_prompt` — preferred; fields (title/company/location/
          language/description) carried in named blocks so the model can't
          confuse them.
      `_build_user_prompt` — legacy free-form; kept for `classify_job(text)`.
  * Description is hard-capped at 4000 chars to keep token spend predictable.

Rate limit: client-side token bucket (`GROQ_RPM` env, default 50 req/min).
Logging: tokens in/out + latency per call (`groq.call`); cumulative cost
emitted at run end (`pipeline.run_complete`).
"""

from __future__ import annotations

import json
import random
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
    Category,
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

# SDD §A.7 — retry-on-low-confidence threshold (mirrors quality_gate default).
_DEFAULT_CONFIDENCE_THRESHOLD: float = 0.7

# Hint appended on the confidence-retry call.
_RETRY_HINT: str = (
    "If a field is genuinely unknowable from the text, return the literal "
    "string 'unknown' (or null where the schema allows). Do not invent values "
    "to raise confidence."
)

# SPEC 02 §5 — strict schema sent in prompt + Pydantic guard (defense in depth)
_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "skills",
        "category",
        "seniority",
        "role_family",
        "employment_type",
        "remote_mode",
        "salary_min",
        "salary_max",
        "currency",
        "languages_required",
        "quality_flags",
        "confidence",
    ],
    "properties": {
        "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "category": {
            "type": ["string", "null"],
            "enum": [
                "software-engineering",
                "devops-sysadmin",
                "data-ml",
                "design",
                "product-management",
                "engineering-management",
                "security",
                "qa-testing",
                "mobile",
                "other-it",
                None,
            ],
        },
        "seniority": {
            "type": "string",
            "enum": ["junior", "mid", "senior", "lead", "principal", "unknown"],
        },
        "role_family": {
            "type": "string",
            "enum": [
                "frontend",
                "backend",
                "fullstack",
                "devops",
                "data",
                "ml",
                "mobile",
                "qa",
                "security",
                "design",
                "pm",
                "other",
            ],
        },
        "employment_type": {
            "type": "string",
            "enum": [
                "full_time",
                "part_time",
                "contract",
                "freelance",
                "internship",
                "unknown",
            ],
        },
        "remote_mode": {
            "type": "string",
            "enum": ["onsite", "hybrid", "remote", "unknown"],
        },
        "salary_min": {"type": ["integer", "null"], "minimum": 0},
        "salary_max": {"type": ["integer", "null"], "minimum": 0},
        "currency": {"type": ["string", "null"]},
        "languages_required": {"type": "array", "items": {"type": "string"}},
        "quality_flags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "clear_jd",
                    "has_responsibilities",
                    "has_requirements",
                    "has_benefits",
                    "has_tech_stack",
                    "vague",
                    "boilerplate",
                ],
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

# SPEC 02 §4 system prompt (constant — cache-friendly)
_SYSTEM_PROMPT = (
    "You are a strict job-listing classifier. You receive a job offer and return ONLY "
    "a JSON object that conforms to the provided schema. No prose, no markdown, no "
    'explanations. If a field is unknown, use the schema\'s "unknown" enum value or '
    "null per the schema. Do not invent skills or salary numbers. Confidence is your "
    "self-assessment of overall extraction reliability (0..1).\n\n"
    "Seniority rules — use the TITLE as the primary signal, then the description:\n"
    '- "senior" only when the title explicitly contains "Senior"/"Sr." or '
    "the description requires 5+ years of experience\n"
    '- "junior" when the title contains "Junior"/"Jr."/"Entry"/"Trainee" '
    'or the posting says "no experience required"\n'
    '- "mid" for roles with 1-4 years of experience and NO seniority keyword in the title\n'
    '- "unknown" when no experience level or seniority keyword is mentioned at all;\n'
    '  do NOT invent a seniority level — "unknown" is correct when the posting is silent'
)

_SCHEMA_STR = json.dumps(_CLASSIFICATION_SCHEMA, separators=(",", ":"))


def _build_user_prompt(text: str, correction: str = "") -> str:
    """Build SPEC 02 §4 structured user prompt for plain-text input."""
    truncated = text[:4000]
    correction_block = f"\nIMPORTANT: {correction}\n" if correction else ""
    return (
        f"{correction_block}"
        f"Job offer:\n{truncated}\n\n"
        f"Return JSON conforming to schema:\n{_SCHEMA_STR}"
    )


def _build_structured_prompt(
    title: str,
    company_name: str,
    location_raw: str,
    detected_language: str,
    description: str,
    correction: str = "",
) -> str:
    """Build SPEC 02 §4 structured user prompt from individual fields."""
    desc_truncated = description[:4000]
    correction_block = f"\nIMPORTANT: {correction}\n" if correction else ""
    return (
        f"{correction_block}"
        f"TITLE: {title}\n"
        f"COMPANY: {company_name}\n"
        f"LOCATION: {location_raw}\n"
        f"DETECTED_LANGUAGE: {detected_language}\n"
        f"DESCRIPTION (truncated to 4000 chars):\n{desc_truncated}\n\n"
        f"Return JSON conforming to schema:\n{_SCHEMA_STR}"
    )


# ---------------------------------------------------------------------------
# Raw Groq output validator (Pydantic, defense in depth per D-01-15)
# ---------------------------------------------------------------------------


class _GroqOutput(BaseModel):
    """Validates raw Groq JSON before mapping to JobClassification.

    Invalid enum values are coerced to defaults (AI sometimes returns novel strings).
    """

    skills: list[str] = []
    category: Category | None = None
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

    @field_validator("category", mode="before")
    @classmethod
    def coerce_category(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str) and v not in {e.value for e in Category}:
            return None
        return v

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

    def classify(
        self, job_raw: dict[str, Any], *, _retry: bool = False
    ) -> JobClassification | None:
        """Classify a job from raw dict (SPEC 02 §4 structured prompt + SDD §A.7).

        Performs one full classification call. If the returned confidence is
        below `_DEFAULT_CONFIDENCE_THRESHOLD` and we haven't retried yet, issues
        a second call with the `retry_with_hints` variant (see module docstring).

        Args:
            job_raw: Dict with keys title, company_name, location_raw,
                     detected_language, description. `url` is used for logging.
            _retry: Internal flag — set True on the recursive retry call to
                    prevent infinite recursion.

        Returns:
            JobClassification on success, None on persistent failure (caller
            marks reject_reason=AI_CLASSIFICATION_FAILED per SDD §I.3).
        """
        result = self._call_groq(job_raw, hint="")
        if result is None:
            return None
        if not _retry and result.ai_confidence < _DEFAULT_CONFIDENCE_THRESHOLD:
            logger.info(
                "groq.confidence_retry",
                first_confidence=round(result.ai_confidence, 2),
                threshold=_DEFAULT_CONFIDENCE_THRESHOLD,
                url=job_raw.get("url", ""),
            )
            retried = self._call_groq(job_raw, hint=_RETRY_HINT)
            if retried is not None:
                return retried
        return result

    def _call_groq(self, job_raw: dict[str, Any], *, hint: str = "") -> JobClassification | None:
        """Single classification round-trip with 3 API-level retries.

        On `RateLimitError`/`APITimeoutError`/`InternalServerError` we backoff and
        retry; on JSON / Pydantic validation errors we re-prompt with a
        corrective hint. After 3 failed attempts returns None.
        """
        correction = hint
        for attempt in range(1, 4):
            try:
                prompt = _build_structured_prompt(
                    title=job_raw.get("title", ""),
                    company_name=job_raw.get("company_name", ""),
                    location_raw=job_raw.get("location_raw", "unknown"),
                    detected_language=job_raw.get("detected_language", "unknown"),
                    description=job_raw.get("description", ""),
                    correction=correction,
                )
                return self._single_call(prompt)
            except (groq.RateLimitError, groq.APITimeoutError, groq.InternalServerError) as exc:
                wait = min(2**attempt, 30) + random.random()
                logger.warning(
                    "groq.api_retry",
                    attempt=attempt,
                    error=str(exc)[:120],
                    wait_s=round(wait, 1),
                    url=job_raw.get("url", ""),
                )
                if attempt < 3:
                    time.sleep(wait)
                else:
                    logger.error("groq.exhausted_api", url=job_raw.get("url", ""))
                    return None
            except (json.JSONDecodeError, ValidationError) as exc:
                correction = f"Your previous response was invalid. Error: {str(exc)[:200]}"
                logger.warning(
                    "groq.validation_retry",
                    attempt=attempt,
                    error=str(exc)[:120],
                    url=job_raw.get("url", ""),
                )
                if attempt == 3:
                    logger.error("groq.exhausted_validation", url=job_raw.get("url", ""))
                    return None
        return None

    def classify_job(self, text: str) -> JobClassification:
        """Classify a job offer from free-form text (legacy interface).

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
        return self._single_call(_build_user_prompt(text))

    def _single_call(self, user_prompt: str) -> JobClassification:
        """Execute one Groq API call; raises on error (no retry here)."""
        self._rate_limiter.acquire()

        t0 = time.monotonic()
        response = self._client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
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
            cost_usd=round(
                tokens_in * _INPUT_PRICE_PER_1M / 1_000_000
                + tokens_out * _OUTPUT_PRICE_PER_1M / 1_000_000,
                6,
            ),
        )

        raw_content = response.choices[0].message.content
        try:
            raw_dict = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.warning("groq.json_parse_error", error=str(e), content=raw_content[:200])
            raise

        # Some model invocations wrap the JSON object in a list — unwrap gracefully
        if isinstance(raw_dict, list) and len(raw_dict) == 1 and isinstance(raw_dict[0], dict):
            raw_dict = raw_dict[0]

        try:
            parsed = _GroqOutput.model_validate(raw_dict)
        except ValidationError as e:
            logger.warning("groq.validation_error", error=str(e))
            raise

        # skills lexicon split deferred to claude-06; all Groq skills → technical_skills
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
