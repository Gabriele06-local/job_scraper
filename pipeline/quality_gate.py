"""Quality gate stage — SDD strict (§A.5).

Replaces the legacy SPEC 03 cascade with a SDD strict gate enforcing:
    MISSING_COMPANY → ZERO_SKILLS (>=2 post-lexicon-split) → DESCRIPTION_INVALID
    → UNKNOWN_SENIORITY_AFTER_RETRY → UNKNOWN_EMPLOYMENT_TYPE_AFTER_RETRY
    → UNKNOWN_REMOTE_MODE_AFTER_RETRY → LOW_CONFIDENCE

Pass paths:
    * job.status = ACTIVE
    * job.quality.quality_tier = PREMIUM if premium criteria met else VALID
    * Soft geocode path: if location.raw is present but location.geo missing,
      we flip `quality.geocode_pending = True` and keep the job ACTIVE — a
      separate `cmd_geocode` CLI run backfills coordinates later (SDD §A.8).

Salary is optional and never causes rejection.

`failure_reasons` vocabulary is closed (SDD §I.3); never invent new keys.
"""

from __future__ import annotations

import re
from enum import Enum

import structlog

from models.job import (
    EmploymentType,
    Job,
    JobClassification,
    JobQuality,
    JobStatus,
    QualityTier,
    RemoteMode,
    Seniority,
)

logger = structlog.get_logger(__name__)

_MIN_VALID_SKILLS = 2  # SDD §A.5 — ZERO_SKILLS; was 1 during lexicon deferral (D-03-04)
_MIN_PREMIUM_SKILLS = 4
_DEFAULT_CONFIDENCE_THRESHOLD = 0.7
_PREMIUM_CONFIDENCE_THRESHOLD = 0.85

_INVALID_COMPANIES = {
    "unknown company",
    "n/a",
    "various",
    "private",
    "anonymous",
}

_BOILERPLATE_PHRASES = (
    "equal opportunity employer",
    "we are looking for",
    "our company is",
    "join our team",
    "competitive salary",
)

_MIN_DESCRIPTION_CHARS = 200
_MIN_DESCRIPTION_SENTENCES = 3
_BOILERPLATE_RATIO_MAX = 0.4
_SENTENCE_END_CHARS = ".!?）」』>"
_SENTENCE_SPLIT = re.compile(r"[.!?]+")

_REMOTE_SCORE = {
    RemoteMode.REMOTE: 1.0,
    RemoteMode.HYBRID: 0.7,
    RemoteMode.ONSITE: 0.4,
    RemoteMode.UNKNOWN: 0.0,
}


class QualityRejectReason(str, Enum):
    """Closed vocabulary of quality-gate reject reasons (SDD §I.3).

    Adding a new value requires a coordinated PR across scraper + backend +
    dashboard (per the locked contract).
    """

    # SDD strict gate
    MISSING_COMPANY = "MISSING_COMPANY"
    ZERO_SKILLS = "ZERO_SKILLS"
    DESCRIPTION_INVALID = "DESCRIPTION_INVALID"
    UNKNOWN_SENIORITY_AFTER_RETRY = "UNKNOWN_SENIORITY_AFTER_RETRY"
    UNKNOWN_EMPLOYMENT_TYPE_AFTER_RETRY = "UNKNOWN_EMPLOYMENT_TYPE_AFTER_RETRY"
    UNKNOWN_REMOTE_MODE_AFTER_RETRY = "UNKNOWN_REMOTE_MODE_AFTER_RETRY"
    URL_INVALID = "URL_INVALID"  # Reserved — set by PrePipelineURLValidator
    SOFT_404 = "SOFT_404"  # Reserved — set by ExpirationChecker

    # Pipeline-level
    AI_CLASSIFICATION_FAILED = "AI_CLASSIFICATION_FAILED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DUPLICATE = "DUPLICATE"

    # Legacy aliases preserved for back-compat with downstream callers / docs.
    INSUFFICIENT_SKILLS = "INSUFFICIENT_SKILLS"
    UNKNOWN_SENIORITY = "UNKNOWN_SENIORITY"
    UNKNOWN_ROLE_FAMILY = "UNKNOWN_ROLE_FAMILY"
    UNKNOWN_REMOTE_MODE = "UNKNOWN_REMOTE_MODE"
    UNKNOWN_EMPLOYMENT_TYPE = "UNKNOWN_EMPLOYMENT_TYPE"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Description meaningfulness — SDD §A.5
# ---------------------------------------------------------------------------


def _is_description_meaningful(text: str) -> bool:
    """Heuristic check that the description is a real, non-boilerplate JD.

    Rules (all must pass):
      1. >= 200 chars stripped
      2. Ends with sentence-terminator (not trailing "...")
      3. Boilerplate phrase coverage < 40% of total chars
      4. >= 3 non-trivial sentences (>=4 words each)
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_DESCRIPTION_CHARS:
        return False

    # Rule 2: sentence-end heuristic
    if stripped.endswith("..."):
        return False
    last_char = stripped[-1]
    # Allow closing HTML tag (basic check)
    closes_with_tag = stripped.endswith(">") and "</" in stripped[-50:]
    if last_char not in _SENTENCE_END_CHARS and not closes_with_tag:
        return False

    # Rule 3: boilerplate ratio
    lowered = stripped.lower()
    matched_chars = sum(len(p) for p in _BOILERPLATE_PHRASES if p in lowered)
    total_chars = max(len(stripped), 1)
    if matched_chars / total_chars > _BOILERPLATE_RATIO_MAX:
        return False

    # Rule 4: sentence count
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(stripped) if s.strip()]
    non_trivial = [s for s in sentences if len(s.split()) >= 4]
    if len(non_trivial) < _MIN_DESCRIPTION_SENTENCES:
        return False

    return True


# ---------------------------------------------------------------------------
# Scoring / premium
# ---------------------------------------------------------------------------


def _description_score(description: str) -> float:
    """Score 0-1 based on description length (proxy for completeness).

    Tiers: <200=0.0, 200-999=0.4, 1000-2999=0.7, 3000+=1.0
    """
    length = len(description or "")
    if length >= 3000:
        return 1.0
    if length >= 1000:
        return 0.7
    if length >= 200:
        return 0.4
    return 0.0


def _quality_flags_score(flags: list[str]) -> float:
    """Score 0-1 based on AI quality flags.

    Positive flags add to score, negative flags penalise.
    """
    s = 0.0
    flag_set = set(flags)
    if "clear_jd" in flag_set:
        s += 0.30
    if "has_requirements" in flag_set:
        s += 0.25
    if "has_benefits" in flag_set:
        s += 0.25
    if "has_tech_stack" in flag_set:
        s += 0.20
    if "vague" in flag_set:
        s -= 0.30
    if "boilerplate" in flag_set:
        s -= 0.30
    return max(0.0, min(1.0, s))


def _requirements_score(requirements: list[str], benefits: list[str]) -> float:
    """Score 0-1 based on presence of requirements and benefits lists.

    Items from both lists are counted.
    """
    total = len(requirements) + len(benefits)
    if total >= 3:
        return 1.0
    if total >= 2:
        return 0.7
    if total >= 1:
        return 0.4
    return 0.0


def compute_quality_score(job: Job) -> int:
    """Compute 0-100 quality score from the full Job object.

    Weights (100%):
      skills 20%, seniority 10%, salary 15%, remote 10%, confidence 10%,
      description 10%, quality_flags 10%, requirements 5%, cv_drop 10%.
    """
    cl = job.classification
    skills_score = min(1.0, len(cl.technical_skills) / 5.0)
    seniority_score = 0.0 if cl.seniority == Seniority.UNKNOWN else 1.0

    has_min = cl.salary_min is not None
    has_max = cl.salary_max is not None
    if has_min and has_max:
        salary_score = 1.0
    elif has_min or has_max:
        salary_score = 0.6
    else:
        salary_score = 0.0

    remote_score = _REMOTE_SCORE.get(cl.remote_mode, 0.0)
    confidence_score = max(0.0, min(1.0, cl.ai_confidence))
    desc_score = _description_score(job.content.description)
    flags_score = _quality_flags_score(cl.quality_flags)
    req_score = _requirements_score(cl.requirements, cl.benefits)
    cv_drop_score = max(0.0, min(1.0, cl.cv_drop_score))

    raw = (
        0.20 * skills_score
        + 0.10 * seniority_score
        + 0.15 * salary_score
        + 0.10 * remote_score
        + 0.10 * confidence_score
        + 0.10 * desc_score
        + 0.10 * flags_score
        + 0.05 * req_score
        + 0.10 * cv_drop_score
    )
    return round(raw * 100)


def _is_company_missing(company_name: str) -> bool:
    """True when company is blank or in the invalid-company sentinel set."""
    name = (company_name or "").strip().lower()
    return not name or name in _INVALID_COMPANIES


def passes_quality_gate(
    classification: JobClassification,
    threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    *,
    company_name: str = "",
    description: str = "",
) -> tuple[bool, list[str]]:
    """Apply SDD strict quality gate (§A.5).

    Returns (True, []) on pass.
    Returns (False, [reason]) on reject — single reason, first-match order.
    """
    # 1. Company present + not in invalid sentinel set
    if _is_company_missing(company_name):
        return False, [QualityRejectReason.MISSING_COMPANY.value]

    # 2. Zero skills hard-fail (>=2 post-lexicon-split; premium needs >=4 downstream)
    if len(classification.technical_skills) < _MIN_VALID_SKILLS:
        return False, [QualityRejectReason.ZERO_SKILLS.value]

    # 3. Description must look like a real JD
    if description and not _is_description_meaningful(description):
        return False, [QualityRejectReason.DESCRIPTION_INVALID.value]

    # 4-6. Enrichment fields — these are "after retry" because ai/classifier.py
    #      already retries once when confidence is low (SDD §A.7). If still
    #      unknown at gate time, we reject.
    if classification.seniority == Seniority.UNKNOWN:
        return False, [QualityRejectReason.UNKNOWN_SENIORITY_AFTER_RETRY.value]

    if classification.employment_type == EmploymentType.UNKNOWN:
        return False, [QualityRejectReason.UNKNOWN_EMPLOYMENT_TYPE_AFTER_RETRY.value]

    if classification.remote_mode == RemoteMode.UNKNOWN:
        return False, [QualityRejectReason.UNKNOWN_REMOTE_MODE_AFTER_RETRY.value]

    # 7. Confidence threshold (last line of defence after classifier retry)
    if classification.ai_confidence < threshold:
        return False, [QualityRejectReason.LOW_CONFIDENCE.value]

    return True, []


def is_premium(classification: JobClassification) -> bool:
    """Premium-tier criteria (assumes valid gate already passed)."""
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
    """Apply the SDD strict quality gate; mutate job in-place and return it.

    Side effects:
      * job.quality.quality_score — always recomputed.
      * job.quality.geocode_pending — True if location.raw present but geo missing.
      * job.status — ACTIVE on pass, REJECTED_QUALITY on fail.
      * job.quality.quality_tier — VALID or PREMIUM on pass, None on fail.
      * job.reject_reason — controlled-vocabulary code on fail.
    """
    cl = job.classification

    score = compute_quality_score(job)
    geocode_pending = bool(job.location and job.location.raw and not job.location.geo)
    job.quality = JobQuality(quality_score=score, geocode_pending=geocode_pending)

    passes, reasons = passes_quality_gate(
        cl,
        company_name=job.company.name,
        description=job.content.description,
    )
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

    job.status = JobStatus.ACTIVE
    job.quality.quality_tier = QualityTier.PREMIUM if is_premium(cl) else QualityTier.VALID

    logger.info(
        "quality_gate.passed",
        url=job.url,
        status=job.status.value,
        quality_tier=job.quality.quality_tier.value,
        quality_score=score,
        geocode_pending=geocode_pending,
    )
    return job
