"""Groq job classifier — primary path routes through the model router.

SPEC 05 refactor: the primary ``classify(job_raw)`` path now delegates the call
control flow (within-tier retry + cross-tier confidence escalation) to
``ai/router.ModelRouter`` over the provider-agnostic ``ai/provider`` seam. This
file keeps only the EXTRACT *schema*, prompts, and the mapping from raw Groq
JSON → :class:`JobClassification`.

Escalation replaces the old same-model confidence retry: a first FAST
(``llama-3.1-8b-instant``) call that returns ``confidence < AI_CONFIDENCE_THRESHOLD``
escalates once to STRUCT (``qwen/qwen3-32b``) with a "don't invent" hint
(D-05-1). The downstream ``quality_gate`` remains the authority on rejection.

Legacy ``classify_job(text)`` is retained verbatim on the raw Groq client
(deprecated): its tests assert the raw ``groq.RateLimitError`` propagates, so it
intentionally bypasses the provider's exception translation. Slated for removal
once the legacy ``main.py`` entrypoint is retired.

Prompt rules (SPEC 02 §4): the system prompt is constant (cache-friendly — do
NOT mutate per request); the description is hard-capped at 4000 chars.
"""

from __future__ import annotations

import json
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

from ai.prompts import (
    FIELD_CONFIDENCE_KEYS,
    build_extract_freeform,
    build_extract_structured,
)
from ai.prompts import EXTRACT_SYSTEM as _SYSTEM_PROMPT
from ai.provider import GroqProvider
from ai.router import ModelRouter, ParseError
from ai.tasks import AITask, Tier
from ai.telemetry import AICallRecord, cost_tracker
from config import settings
from models.job import (
    Category,
    EmploymentType,
    JobClassification,
    RemoteMode,
    RoleFamily,
    Seniority,
)
from utils.skills_lexicon import split_skills

logger = structlog.get_logger(__name__)

# Re-exported so `from ai.classifier import cost_tracker` keeps working
# (pipeline/orchestrator.py, scripts/run_ai_baseline.py).
__all__ = ["GroqClassifier", "cost_tracker"]


# Prompts + schema now live in the versioned registry (ai/prompts.py).
# `_SYSTEM_PROMPT` is imported there and re-exported for back-compat (tests
# import it from this module to guard against prompt drift).


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
    cv_drop_score: float = 0.0
    confidence: float = 0.0
    field_confidence: dict[str, float] | None = None

    @field_validator("field_confidence", mode="before")
    @classmethod
    def coerce_field_confidence(cls, v: object) -> object:
        """Keep only known keys with numeric values clamped to 0..1."""
        if not isinstance(v, dict):
            return None
        out: dict[str, float] = {}
        for key in FIELD_CONFIDENCE_KEYS:
            val = v.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out[key] = max(0.0, min(1.0, float(val)))
        return out or None

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


def _parse_extract(content: str) -> tuple[_GroqOutput, float]:
    """Parse + validate raw EXTRACT content; raises ParseError on bad output."""
    try:
        raw_dict = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ParseError(str(exc)) from exc
    # Some model invocations wrap the JSON object in a list — unwrap gracefully.
    if isinstance(raw_dict, list) and len(raw_dict) == 1 and isinstance(raw_dict[0], dict):
        raw_dict = raw_dict[0]
    try:
        parsed = _GroqOutput.model_validate(raw_dict)
    except ValidationError as exc:
        raise ParseError(str(exc)) from exc
    return parsed, parsed.confidence


def _to_classification(parsed: _GroqOutput, model: str) -> JobClassification:
    """Map validated Groq output to the pipeline's JobClassification."""
    tech, non_tech = split_skills(parsed.skills)
    return JobClassification(
        technical_skills=tech,
        skills=non_tech,
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
        cv_drop_score=parsed.cv_drop_score,
        ai_confidence=parsed.confidence,
        field_confidence=parsed.field_confidence,
        ai_model=model,
        ai_call_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class GroqClassifier:
    """Job classifier over the model router (primary) + legacy text path."""

    def __init__(self, client: groq.Groq | None = None) -> None:
        # The raw client is shared with the provider so the legacy classify_job
        # path and the routed path hit the same (mockable) Groq client.
        self._client = client or groq.Groq(api_key=settings.groq_api_key)
        self._provider = GroqProvider(client=self._client)
        self._router = ModelRouter(provider=self._provider)
        # Tests mutate `clf._rate_limiter._min_interval`; expose the provider's.
        self._rate_limiter = self._provider._rate_limiter

    def classify(
        self, job_raw: dict[str, Any], *, _retry: bool = False
    ) -> JobClassification | None:
        """Classify a job (SPEC 05 EXTRACT task, FAST→STRUCT escalation).

        Args:
            job_raw: Dict with keys title, company_name, location_raw,
                     detected_language, description; `url` is used as trace id.
            _retry: When True, disables escalation (single tier only).

        Returns:
            JobClassification on success, None on persistent failure (caller
            marks reject_reason=AI_CLASSIFICATION_FAILED per SDD §I.3).
        """

        def build_prompt(_tier: Tier, correction: str) -> tuple[str, str]:
            return _SYSTEM_PROMPT, build_extract_structured(
                title=job_raw.get("title", ""),
                company_name=job_raw.get("company_name", ""),
                location_raw=job_raw.get("location_raw", "unknown"),
                detected_language=job_raw.get("detected_language", "unknown"),
                description=job_raw.get("description", ""),
                correction=correction,
            )

        result = self._router.run(
            task=AITask.EXTRACT,
            build_prompt=build_prompt,
            parse=_parse_extract,
            trace_id=job_raw.get("url", ""),
            allow_escalation=not _retry,
        )
        if result is None:
            return None
        return _to_classification(result.data, result.model)

    # -- legacy free-form path (deprecated) --------------------------------

    def classify_job(self, text: str) -> JobClassification:
        """Classify a job offer from free-form text (legacy interface).

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
        return self._single_call(build_extract_freeform(text))

    def _single_call(self, user_prompt: str) -> JobClassification:
        """Execute one raw Groq call (legacy path); raises on error."""
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

        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

        raw_content = response.choices[0].message.content
        parsed, _confidence = _parse_extract(raw_content)

        cost_tracker.record(
            AICallRecord(
                task=AITask.EXTRACT.value,
                tier=Tier.FAST.value,
                model=settings.groq_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
            )
        )
        return _to_classification(parsed, settings.groq_model)
