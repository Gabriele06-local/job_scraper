"""SDD strict quality-gate coverage (§A.5 / §A.9).

`test_quality_gate.py` carries the legacy + new combined suite; this file
focuses specifically on the controlled-vocabulary reject reasons added by the
SDD strict refactor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models.job import (
    EmploymentType,
    Job,
    JobClassification,
    JobCompany,
    JobContent,
    JobLocation,
    JobSource,
    JobStatus,
    Language,
    QualityTier,
    RemoteMode,
    RoleFamily,
    Seniority,
)
from pipeline.quality_gate import (
    QualityRejectReason,
    _is_description_meaningful,
    evaluate,
)

_NOW = datetime.now(tz=timezone.utc)

_GOOD_DESC = (
    "Senior backend engineer wanted to ship Python services in production. "
    "You will own the API surface across our distributed platform. "
    "You will mentor junior engineers across the wider engineering team. "
    "We offer remote work with competitive total compensation packages."
)


def _cls(**kw) -> JobClassification:
    base = dict(
        technical_skills=["Python", "Django", "Postgres"],
        seniority=Seniority.SENIOR,
        role_family=RoleFamily.BACKEND,
        remote_mode=RemoteMode.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        salary_min=None,
        salary_max=None,
        ai_confidence=0.80,
        quality_flags=[],
    )
    base.update(kw)
    return JobClassification(**base)


def _job(*, company: str = "Acme Corp", description: str = _GOOD_DESC, **kw) -> Job:
    cl = kw.pop("classification", _cls())
    return Job(
        url="https://jobs.example.com/x",
        dedup_hash="abc",
        source_info=JobSource(source="adzuna"),
        content=JobContent(
            title="Senior Backend Engineer",
            title_normalized="senior backend engineer",
            description=description,
            language=Language.EN,
        ),
        company=JobCompany(name=company, name_normalized=company.lower()),
        posted_at=_NOW,
        classification=cl,
        **kw,
    )


# ---------------------------------------------------------------------------
# One test per new rejection reason (closed-vocab — SDD §I.3)
# ---------------------------------------------------------------------------


def test_missing_company_blank() -> None:
    job = _job(company="")
    out = evaluate(job)
    assert out.status == JobStatus.REJECTED_QUALITY
    assert out.reject_reason == QualityRejectReason.MISSING_COMPANY.value


def test_missing_company_sentinel() -> None:
    job = _job(company="Various")
    out = evaluate(job)
    assert out.reject_reason == QualityRejectReason.MISSING_COMPANY.value


def test_zero_skills_rejects() -> None:
    job = _job(classification=_cls(technical_skills=[]))
    out = evaluate(job)
    assert out.reject_reason == QualityRejectReason.ZERO_SKILLS.value


def test_description_invalid_short() -> None:
    job = _job(description="too short")
    out = evaluate(job)
    assert out.reject_reason == QualityRejectReason.DESCRIPTION_INVALID.value


def test_unknown_seniority_after_retry() -> None:
    job = _job(classification=_cls(seniority=Seniority.UNKNOWN))
    out = evaluate(job)
    assert out.reject_reason == QualityRejectReason.UNKNOWN_SENIORITY_AFTER_RETRY.value


def test_unknown_employment_type_after_retry() -> None:
    job = _job(classification=_cls(employment_type=EmploymentType.UNKNOWN))
    out = evaluate(job)
    assert (
        out.reject_reason == QualityRejectReason.UNKNOWN_EMPLOYMENT_TYPE_AFTER_RETRY.value
    )


def test_unknown_remote_mode_after_retry() -> None:
    job = _job(classification=_cls(remote_mode=RemoteMode.UNKNOWN))
    out = evaluate(job)
    assert out.reject_reason == QualityRejectReason.UNKNOWN_REMOTE_MODE_AFTER_RETRY.value


def test_low_confidence_rejects() -> None:
    job = _job(classification=_cls(ai_confidence=0.5))
    out = evaluate(job)
    assert out.reject_reason == QualityRejectReason.LOW_CONFIDENCE.value


# ---------------------------------------------------------------------------
# Pass paths
# ---------------------------------------------------------------------------


def test_valid_path_marks_active_with_valid_tier() -> None:
    out = evaluate(_job())
    assert out.status == JobStatus.ACTIVE
    assert out.quality.quality_tier == QualityTier.VALID


def test_premium_path_marks_active_with_premium_tier() -> None:
    cl = _cls(
        technical_skills=["Go", "K8s", "AWS", "Postgres"],
        salary_min=80000,
        salary_max=120000,
        ai_confidence=0.92,
        quality_flags=["clear_jd", "has_requirements"],
    )
    out = evaluate(_job(classification=cl))
    assert out.status == JobStatus.ACTIVE
    assert out.quality.quality_tier == QualityTier.PREMIUM


def test_geocode_soft_path_keeps_job_active() -> None:
    job = _job()
    job.location = JobLocation(raw="Milan, Italy")  # raw present, no geo
    out = evaluate(job)
    assert out.status == JobStatus.ACTIVE
    assert out.quality.geocode_pending is True


# ---------------------------------------------------------------------------
# _is_description_meaningful 10-case truth table (SDD §A.5)
# ---------------------------------------------------------------------------


def test_meaningful_truth_table() -> None:
    cases: list[tuple[str, bool, str]] = [
        (_GOOD_DESC, True, "happy path"),
        ("", False, "empty"),
        ("short", False, "tiny"),
        ("a" * 199, False, "below 200 char floor"),
        ("a" * 250 + "...", False, "trailing ellipsis"),
        (
            "one full long sentence " * 30,  # repeating → single 'sentence' before split
            False,
            "no sentence terminator",
        ),
        (
            "Equal opportunity employer. We are looking for. Our company is. "
            "Equal opportunity employer. We are looking for. Our company is. Equal "
            "opportunity employer. Join our team. Competitive salary. We are looking for.",
            False,
            "boilerplate dominated",
        ),
        (
            "Hi. Job. Apply. " + "x" * 200,
            False,
            "no non-trivial sentences",
        ),
        (
            "<p>Senior Python developer wanted to ship code daily in our production "
            "environment. You will own the backend services and mentor the wider "
            "engineering team across the organisation. We value tests deeply for "
            "every single change we ship.</p>",
            True,
            "ends with closing HTML tag",
        ),
        (
            "We need a senior Python engineer to ship production services daily. "
            "You will own the API surface across our distributed platform stack. "
            "You will mentor junior engineers across the wider engineering team. "
            "We offer fully remote work with competitive total compensation.",
            True,
            "4 strong sentences",
        ),
    ]
    failures: list[str] = []
    for text, expected, label in cases:
        actual = _is_description_meaningful(text)
        if actual is not expected:
            failures.append(f"{label}: expected {expected}, got {actual}")
    assert not failures, "\n".join(failures)
