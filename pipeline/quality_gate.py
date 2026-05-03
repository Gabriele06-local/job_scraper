"""Quality gate stage — SPEC 03.

Rules applied in first-match order (SPEC 03 §4):
  INSUFFICIENT_SKILLS → UNKNOWN_SENIORITY → UNKNOWN_ROLE_FAMILY
  → NO_SALARY_AND_NO_REMOTE_MODE → LOW_CONFIDENCE
"""

from __future__ import annotations

from enum import Enum

import structlog

from models.job import (
    EmploymentType,
    Job,
    JobClassification,
    JobQuality,
    JobStatus,
    RemoteMode,
    RoleFamily,
    Seniority,
)

logger = structlog.get_logger(__name__)

_MIN_VALID_SKILLS = 2
_MIN_PREMIUM_SKILLS = 4
_DEFAULT_CONFIDENCE_THRESHOLD = 0.7
_PREMIUM_CONFIDENCE_THRESHOLD = 0.85

_REMOTE_SCORE = {
    RemoteMode.REMOTE: 1.0,
    RemoteMode.HYBRID: 0.7,
    RemoteMode.ONSITE: 0.4,
    RemoteMode.UNKNOWN: 0.0,
}


class QualityRejectReason(str, Enum):
    """Reject reasons set by the quality gate."""

    INSUFFICIENT_SKILLS = "INSUFFICIENT_SKILLS"
    UNKNOWN_SENIORITY = "UNKNOWN_SENIORITY"
    UNKNOWN_ROLE_FAMILY = "UNKNOWN_ROLE_FAMILY"
    UNKNOWN_REMOTE_MODE = "UNKNOWN_REMOTE_MODE"
    UNKNOWN_EMPLOYMENT_TYPE = "UNKNOWN_EMPLOYMENT_TYPE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"


def compute_quality_score(classification: JobClassification) -> int:
    """Compute 0-100 quality score (SPEC 03 §3).

    Weights: skills 30%, seniority 20%, salary 20%, remote 15%, confidence 15%.
    """
    skills_score = min(1.0, len(classification.technical_skills) / 5.0)

    seniority_score = 0.0 if classification.seniority == Seniority.UNKNOWN else 1.0

    has_min = classification.salary_min is not None
    has_max = classification.salary_max is not None
    if has_min and has_max:
        salary_score = 1.0
    elif has_min or has_max:
        salary_score = 0.6
    else:
        salary_score = 0.0

    remote_score = _REMOTE_SCORE.get(classification.remote_mode, 0.0)

    confidence_score = max(0.0, min(1.0, classification.ai_confidence))

    raw = (
        0.30 * skills_score
        + 0.20 * seniority_score
        + 0.20 * salary_score
        + 0.15 * remote_score
        + 0.15 * confidence_score
    )
    return round(raw * 100)


def passes_quality_gate(
    classification: JobClassification,
    threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[bool, list[str]]:
    """Check SPEC 03 §2.1 valid gate rules; first-match reject_reason.

    Returns:
        (True, []) on pass.
        (False, [reason]) on reject — exactly one reason, first-match order.
    """
    if len(classification.technical_skills) < _MIN_VALID_SKILLS:
        return False, [QualityRejectReason.INSUFFICIENT_SKILLS.value]

    if classification.seniority == Seniority.UNKNOWN:
        return False, [QualityRejectReason.UNKNOWN_SENIORITY.value]

    if classification.role_family == RoleFamily.OTHER:
        return False, [QualityRejectReason.UNKNOWN_ROLE_FAMILY.value]

    if classification.remote_mode == RemoteMode.UNKNOWN:
        return False, [QualityRejectReason.UNKNOWN_REMOTE_MODE.value]

    if classification.employment_type == EmploymentType.UNKNOWN:
        return False, [QualityRejectReason.UNKNOWN_EMPLOYMENT_TYPE.value]

    if classification.ai_confidence < threshold:
        return False, [QualityRejectReason.LOW_CONFIDENCE.value]

    return True, []


def is_premium(classification: JobClassification) -> bool:
    """Check SPEC 03 §2.2 premium criteria (assumes valid gate already passed)."""
    if len(classification.technical_skills) < _MIN_PREMIUM_SKILLS:
        return False
    if classification.salary_min is None or classification.salary_max is None:
        return False
    if classification.ai_confidence < _PREMIUM_CONFIDENCE_THRESHOLD:
        return False
    flags = set(classification.quality_flags)
    if not flags & {"clear_jd", "has_requirements"}:
        return False
    if "boilerplate" in flags:
        return False
    return True


def evaluate(job: Job) -> Job:
    """Apply quality gate; update job.status, reject_reason, quality_score in-place."""
    cl = job.classification

    score = compute_quality_score(cl)
    job.quality = JobQuality(quality_score=score)

    passes, reasons = passes_quality_gate(cl)
    if not passes:
        job.status = JobStatus.REJECTED_QUALITY
        job.reject_reason = reasons[0]
        logger.info(
            "quality_gate.rejected",
            url=job.url,
            reason=job.reject_reason,
            quality_score=score,
        )
        return job

    if is_premium(cl):
        job.status = JobStatus.PREMIUM
    else:
        job.status = JobStatus.VALID

    logger.info(
        "quality_gate.passed",
        url=job.url,
        status=job.status.value,
        quality_score=score,
    )
    return job
