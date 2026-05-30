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
        "cv_drop_score": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": (
                "How likely a qualified candidate submits their CV here "
                "(0=poor, 1=compelling). Based on clarity, salary, benefits, "
                "tech stack appeal, and posting completeness."
            ),
        },
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
    '  do NOT invent a seniority level — "unknown" is correct when the posting is silent\n\n'
    "cv_drop_score (0..1): rate how likely a qualified candidate would submit their CV.\n"
    "High scores need clear salary, benefits, tech stack, and a well-written description.\n"
    "Low scores: vague/boilerplate text, no salary or benefits, poor formatting."
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
    cv_drop_score: float = 0.0
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
            return _SYSTEM_PROMPT, _build_structured_prompt(
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
        return self._single_call(_build_user_prompt(text))

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
