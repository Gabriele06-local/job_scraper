"""Centralized, versioned prompt registry (SPEC 05 §4.4).

All prompt text + JSON schemas live here, keyed by ``(prompt_id, version)`` so
the cache key (SPEC 05 §4.5) and telemetry can pin a prompt version. System
prompts are constant strings (Groq prompt-cache friendly — never interpolate
per request). Reusable user-prompt builders keep field ordering stable.

Tasks (ai/tasks.AITask) reference these by ``prompt_id`` + ``prompt_version``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# SPEC 05 §4.3 — per-field confidence keys the model must self-assess.
FIELD_CONFIDENCE_KEYS: tuple[str, ...] = (
    "salary",
    "remote_mode",
    "seniority",
    "company_quality",
    "technologies",
)


# ---------------------------------------------------------------------------
# EXTRACT (the main classification call) — schema is SPEC 02 §5 + field_confidence
# ---------------------------------------------------------------------------

EXTRACT_SCHEMA: dict = {
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
        # SPEC 05 §4.3 — optional per-field confidence object.
        "field_confidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: {"type": "number", "minimum": 0, "maximum": 1}
                for key in FIELD_CONFIDENCE_KEYS
            },
            "description": (
                "Per-field extraction confidence 0..1 for: "
                + ", ".join(FIELD_CONFIDENCE_KEYS)
                + ". Use a low value when the field was guessed or absent."
            ),
        },
    },
}

EXTRACT_SYSTEM = (
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
    "Low scores: vague/boilerplate text, no salary or benefits, poor formatting.\n\n"
    "field_confidence (0..1 per field: salary, remote_mode, seniority, company_quality, "
    "technologies): how sure you are of each — low when the value was guessed or absent."
)

_EXTRACT_SCHEMA_STR = json.dumps(EXTRACT_SCHEMA, separators=(",", ":"))


def build_extract_structured(
    title: str,
    company_name: str,
    location_raw: str,
    detected_language: str,
    description: str,
    correction: str = "",
) -> str:
    """SPEC 02 §4 structured EXTRACT user prompt from individual fields."""
    desc_truncated = description[:4000]
    correction_block = f"\nIMPORTANT: {correction}\n" if correction else ""
    return (
        f"{correction_block}"
        f"TITLE: {title}\n"
        f"COMPANY: {company_name}\n"
        f"LOCATION: {location_raw}\n"
        f"DETECTED_LANGUAGE: {detected_language}\n"
        f"DESCRIPTION (truncated to 4000 chars):\n{desc_truncated}\n\n"
        f"Return JSON conforming to schema:\n{_EXTRACT_SCHEMA_STR}"
    )


def build_extract_freeform(text: str, correction: str = "") -> str:
    """SPEC 02 §4 EXTRACT user prompt for plain-text input (legacy)."""
    truncated = text[:4000]
    correction_block = f"\nIMPORTANT: {correction}\n" if correction else ""
    return (
        f"{correction_block}"
        f"Job offer:\n{truncated}\n\n"
        f"Return JSON conforming to schema:\n{_EXTRACT_SCHEMA_STR}"
    )


# ---------------------------------------------------------------------------
# TRIAGE (cheap FAST pre-screen, opt-in) — SPEC 05 §4.4
# ---------------------------------------------------------------------------

TRIAGE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_it_job", "spam_likelihood", "complexity"],
    "properties": {
        "is_it_job": {"type": "boolean"},
        "category_guess": {"type": ["string", "null"]},
        "spam_likelihood": {"type": "number", "minimum": 0, "maximum": 1},
        "complexity": {"type": "string", "enum": ["easy", "hard"]},
    },
}

TRIAGE_SYSTEM = (
    "You are a fast job-listing triage filter. Return ONLY a JSON object matching "
    "the schema. Decide if this is a genuine IT/software job, estimate spam "
    "likelihood (0..1), and rate parsing complexity: 'easy' for clean, well-formed "
    "postings, 'hard' for noisy HTML, multi-role, or ambiguous text."
)


def build_triage(title: str, company_name: str, description: str) -> str:
    """Tiny TRIAGE user prompt (short description budget)."""
    return (
        f"TITLE: {title}\nCOMPANY: {company_name}\n"
        f"DESCRIPTION (truncated to 1500 chars):\n{description[:1500]}\n\n"
        f"Return JSON conforming to schema:\n"
        f"{json.dumps(TRIAGE_SCHEMA, separators=(',', ':'))}"
    )


# ---------------------------------------------------------------------------
# SPAM_CHECK (REASON tier, on suspicion) — SPEC 05 §4.4
# ---------------------------------------------------------------------------

SPAM_CHECK_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_spam", "fake_remote", "suspicious_recruiter", "confidence"],
    "properties": {
        "is_spam": {"type": "boolean"},
        "fake_remote": {"type": "boolean"},
        "suspicious_recruiter": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

SPAM_CHECK_SYSTEM = (
    "You are an expert at detecting low-quality, spam, and misleading job postings. "
    "Return ONLY a JSON object matching the schema. Flag: outright spam/scams; "
    "'fake remote' (titled remote but body requires on-site/relocation); and "
    "suspicious recruiter behaviour (vague client, mass-blast wording, no real role). "
    "Be conservative — only flag with clear textual evidence."
)


def build_spam_check(
    title: str, company_name: str, location_raw: str, description: str
) -> str:
    """SPAM_CHECK user prompt."""
    return (
        f"TITLE: {title}\nCOMPANY: {company_name}\nLOCATION: {location_raw}\n"
        f"DESCRIPTION (truncated to 4000 chars):\n{description[:4000]}\n\n"
        f"Return JSON conforming to schema:\n"
        f"{json.dumps(SPAM_CHECK_SCHEMA, separators=(',', ':'))}"
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptTemplate:
    """One versioned prompt: constant system text + JSON schema."""

    prompt_id: str
    version: str
    system: str
    schema: dict

    @property
    def schema_str(self) -> str:
        return json.dumps(self.schema, separators=(",", ":"))


PROMPTS: dict[tuple[str, str], PromptTemplate] = {
    ("extract", "v1"): PromptTemplate("extract", "v1", EXTRACT_SYSTEM, EXTRACT_SCHEMA),
    ("triage", "v1"): PromptTemplate("triage", "v1", TRIAGE_SYSTEM, TRIAGE_SCHEMA),
    ("spam_check", "v1"): PromptTemplate(
        "spam_check", "v1", SPAM_CHECK_SYSTEM, SPAM_CHECK_SCHEMA
    ),
}


def get_prompt(prompt_id: str, version: str) -> PromptTemplate:
    """Look up a prompt template; raises KeyError if the version is unknown."""
    try:
        return PROMPTS[(prompt_id, version)]
    except KeyError as exc:
        raise KeyError(f"No prompt {prompt_id!r} version {version!r}") from exc
