"""Tests for pipeline/quality_gate.py — SDD strict gate (§A.5)."""

from __future__ import annotations

from datetime import datetime, timezone

from models.job import (
    EmploymentType,
    Job,
    JobClassification,
    JobCompany,
    JobContent,
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
    compute_quality_score,
    evaluate,
    is_premium,
    passes_quality_gate,
)

_NOW = datetime.now(tz=timezone.utc)

# Long, meaningful JD that passes _is_description_meaningful.
_LONG_DESC = (
    "We are looking for a Senior Python Developer to join our backend team. "
    "You will design REST APIs with Django and PostgreSQL. "
    "Your responsibilities include shipping production code daily and "
    "mentoring junior engineers across the team. Requirements: 5+ years "
    "Python, Docker, Kubernetes, AWS, strong testing discipline. "
    "Remote, EUR 80k-120k, equity, learning budget."
)


def _cls(**kwargs) -> JobClassification:
    """JobClassification with sensible defaults; override via kwargs."""
    defaults = dict(
        technical_skills=["Python", "Django"],
        seniority=Seniority.SENIOR,
        role_family=RoleFamily.BACKEND,
        remote_mode=RemoteMode.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        salary_min=None,
        salary_max=None,
        currency=None,
        ai_confidence=0.80,
        quality_flags=[],
    )
    defaults.update(kwargs)
    return JobClassification(**defaults)


def _job(classification: JobClassification | None = None, **overrides) -> Job:
    cl = classification or _cls()
    base = dict(
        url="https://jobs.example.com/1",
        dedup_hash="abc123",
        source_info=JobSource(source="adzuna"),
        content=JobContent(
            title="Senior Python Developer",
            title_normalized="senior python developer",
            description=_LONG_DESC,
            language=Language.EN,
        ),
        company=JobCompany(name="Acme Corp", name_normalized="acme corp"),
        posted_at=_NOW,
        classification=cl,
    )
    base.update(overrides)
    return Job(**base)


# ---------------------------------------------------------------------------
# compute_quality_score
# ---------------------------------------------------------------------------


class TestComputeQualityScore:
    def test_perfect_score(self):
        long_desc = "x " * 2000  # >3000 chars
        cl = _cls(
            technical_skills=["A", "B", "C", "D", "E"],
            seniority=Seniority.SENIOR,
            salary_min=80000,
            salary_max=120000,
            remote_mode=RemoteMode.REMOTE,
            ai_confidence=1.0,
            cv_drop_score=1.0,
            quality_flags=["clear_jd", "has_requirements", "has_benefits", "has_tech_stack"],
            requirements=["req1", "req2"],
            benefits=["ben1"],
        )
        job = _job(classification=cl, content=JobContent(
            title="Senior Python Developer",
            title_normalized="senior python developer",
            description=long_desc,
            language=Language.EN,
        ))
        assert compute_quality_score(job) == 100

    def test_zero_score(self):
        cl = _cls(
            technical_skills=[],
            seniority=Seniority.UNKNOWN,
            salary_min=None,
            salary_max=None,
            remote_mode=RemoteMode.UNKNOWN,
            ai_confidence=0.0,
            quality_flags=[],
            requirements=[],
            benefits=[],
        )
        job = _job(classification=cl, content=JobContent(
            title="X",
            title_normalized="x",
            description="short",
            language=Language.EN,
        ))
        assert compute_quality_score(job) == 0

    def test_returns_int(self):
        assert isinstance(compute_quality_score(_job()), int)


# ---------------------------------------------------------------------------
# passes_quality_gate — SDD strict (§A.5)
# ---------------------------------------------------------------------------


class TestPassesQualityGate:
    def test_valid_classification_passes(self):
        ok, reasons = passes_quality_gate(_cls(), company_name="Acme", description=_LONG_DESC)
        assert ok is True
        assert reasons == []

    def test_missing_company_rejects(self):
        ok, reasons = passes_quality_gate(_cls(), company_name="", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.MISSING_COMPANY.value]

    def test_invalid_company_sentinel_rejects(self):
        for sentinel in ("Unknown Company", "n/a", "Various", "Private", "Anonymous"):
            ok, reasons = passes_quality_gate(_cls(), company_name=sentinel, description=_LONG_DESC)
            assert not ok, f"sentinel {sentinel!r} should reject"
            assert reasons == [QualityRejectReason.MISSING_COMPANY.value]

    def test_zero_skills_rejects(self):
        cl = _cls(technical_skills=[])
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.ZERO_SKILLS.value]

    def test_one_skill_now_rejects_after_lexicon_split(self):
        """Post-lexicon-split (D-03-04): ZERO_SKILLS fires at <2 (was <1)."""
        cl = _cls(technical_skills=["Python"])
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.ZERO_SKILLS.value]

    def test_two_skills_passes_minimum(self):
        cl = _cls(technical_skills=["Python", "Django"])
        ok, _ = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert ok is True

    def test_unknown_seniority_after_retry_rejects(self):
        cl = _cls(seniority=Seniority.UNKNOWN)
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.UNKNOWN_SENIORITY_AFTER_RETRY.value]

    def test_unknown_employment_type_after_retry_rejects(self):
        cl = _cls(employment_type=EmploymentType.UNKNOWN)
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.UNKNOWN_EMPLOYMENT_TYPE_AFTER_RETRY.value]

    def test_unknown_remote_mode_after_retry_rejects(self):
        cl = _cls(remote_mode=RemoteMode.UNKNOWN)
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.UNKNOWN_REMOTE_MODE_AFTER_RETRY.value]

    def test_low_confidence_rejects(self):
        cl = _cls(ai_confidence=0.6)
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.LOW_CONFIDENCE.value]

    def test_role_family_other_is_now_allowed(self):
        """SDD strict no longer rejects role_family=OTHER (was legacy gate)."""
        cl = _cls(role_family=RoleFamily.OTHER)
        ok, _ = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert ok is True

    def test_onsite_no_salary_passes(self):
        cl = _cls(salary_min=None, salary_max=None, remote_mode=RemoteMode.ONSITE)
        ok, _ = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert ok is True

    def test_first_match_order_company_before_skills(self):
        cl = _cls(technical_skills=[])  # would trigger ZERO_SKILLS later
        ok, reasons = passes_quality_gate(cl, company_name="", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.MISSING_COMPANY.value]

    def test_first_match_order_skills_before_unknown_seniority(self):
        cl = _cls(technical_skills=[], seniority=Seniority.UNKNOWN)
        ok, reasons = passes_quality_gate(cl, company_name="Acme", description=_LONG_DESC)
        assert not ok
        assert reasons == [QualityRejectReason.ZERO_SKILLS.value]


# ---------------------------------------------------------------------------
# _is_description_meaningful truth table — SDD §A.5
# ---------------------------------------------------------------------------


class TestIsDescriptionMeaningful:
    def test_truth_table(self):
        from pipeline.quality_gate import _is_description_meaningful

        cases: list[tuple[str, bool, str]] = [
            # (text, expected, label)
            (_LONG_DESC, True, "happy-path long JD"),
            ("", False, "empty string"),
            ("short", False, "tiny string"),
            ("a" * 199, False, "just below 200 char floor"),
            (
                # 200+ chars but ends with ellipsis -> invalid
                "This role offers exciting work in a growing team."
                + " Filler sentence one." * 10
                + "...",
                False,
                "trailing ellipsis",
            ),
            (
                # >200 chars but only one full sentence -> too few sentences
                "Senior Python developer wanted to ship code in production daily across the "
                "stack and beyond a single short sentence that keeps going on and on.",
                False,
                "single long sentence",
            ),
            (
                # >200 chars dominated by boilerplate -> ratio > 0.4
                "Equal opportunity employer. We are looking for. Our company is. "
                "Equal opportunity employer. We are looking for. Our company is. Equal "
                "opportunity employer.",
                False,
                "boilerplate dominated",
            ),
            (
                # Closes with HTML tag -> accept end heuristic (>=200 chars)
                (
                    "<p>Senior Python developer wanted to ship code in production daily. "
                    "You will own the backend services and mentor the wider engineering "
                    "team across the organisation. We value tests deeply for every change "
                    "we ship to production.</p>"
                ),
                True,
                "ends with HTML close tag",
            ),
            (
                # 3 short sentences each less than 4 words -> non-trivial fails
                "Hi there. Job here. Apply now. " + "x" * 200,
                False,
                "no non-trivial sentences",
            ),
            (
                # Normal valid description with 4 strong sentences
                "We need a senior backend engineer to ship production code. "
                "You will own the API surface and craft tests with care. "
                "You will mentor junior engineers across the wider organisation. "
                "We offer remote work and competitive total compensation.",
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


# ---------------------------------------------------------------------------
# is_premium
# ---------------------------------------------------------------------------


class TestIsPremium:
    def test_full_premium_criteria(self):
        cl = _cls(
            technical_skills=["A", "B", "C", "D"],
            salary_min=80000,
            salary_max=120000,
            ai_confidence=0.90,
            quality_flags=["clear_jd", "has_requirements"],
        )
        assert is_premium(cl) is True

    def test_too_few_skills_not_premium(self):
        cl = _cls(technical_skills=["A", "B", "C"])
        assert is_premium(cl) is False

    def test_missing_salary_not_premium(self):
        cl = _cls(technical_skills=["A", "B", "C", "D"], salary_min=None, salary_max=120000)
        assert is_premium(cl) is False

    def test_low_confidence_not_premium(self):
        cl = _cls(technical_skills=["A", "B", "C", "D"], ai_confidence=0.80)
        assert is_premium(cl) is False

    def test_boilerplate_flag_not_premium(self):
        cl = _cls(
            technical_skills=["A", "B", "C", "D"],
            ai_confidence=0.90,
            quality_flags=["clear_jd", "boilerplate"],
        )
        assert is_premium(cl) is False


# ---------------------------------------------------------------------------
# evaluate — end-to-end
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_valid_job_gets_active_status_and_valid_tier(self):
        job = _job()
        result = evaluate(job)
        assert result.status == JobStatus.ACTIVE
        assert result.quality.quality_tier == QualityTier.VALID
        assert result.reject_reason is None
        assert result.quality.quality_score > 0

    def test_premium_job_gets_active_status_and_premium_tier(self):
        cl = _cls(
            technical_skills=["Go", "Kubernetes", "AWS", "PostgreSQL"],
            salary_min=90000,
            salary_max=130000,
            ai_confidence=0.92,
            quality_flags=["clear_jd", "has_requirements"],
        )
        result = evaluate(_job(cl))
        assert result.status == JobStatus.ACTIVE
        assert result.quality.quality_tier == QualityTier.PREMIUM

    def test_rejected_job_has_reason_and_no_tier(self):
        cl = _cls(technical_skills=[])  # ZERO_SKILLS
        result = evaluate(_job(cl))
        assert result.status == JobStatus.REJECTED_QUALITY
        assert result.reject_reason == QualityRejectReason.ZERO_SKILLS.value
        assert result.quality.quality_tier is None

    def test_missing_company_rejects(self):
        job = _job()
        job.company = JobCompany(name="", name_normalized="")
        result = evaluate(job)
        assert result.status == JobStatus.REJECTED_QUALITY
        assert result.reject_reason == QualityRejectReason.MISSING_COMPANY.value

    def test_unknown_seniority_after_retry_reject_reason(self):
        cl = _cls(seniority=Seniority.UNKNOWN)
        result = evaluate(_job(cl))
        assert result.reject_reason == QualityRejectReason.UNKNOWN_SENIORITY_AFTER_RETRY.value

    def test_low_confidence_reject_reason(self):
        cl = _cls(ai_confidence=0.5)
        result = evaluate(_job(cl))
        assert result.reject_reason == QualityRejectReason.LOW_CONFIDENCE.value

    def test_geocode_pending_soft_path_keeps_active(self):
        """Location.raw present but no geo → geocode_pending=True, status=ACTIVE."""
        from models.job import JobLocation

        job = _job()
        job.location = JobLocation(raw="Milan, Italy")  # no geo
        result = evaluate(job)
        assert result.status == JobStatus.ACTIVE
        assert result.quality.geocode_pending is True

    def test_geocode_not_pending_when_no_raw_location(self):
        from models.job import JobLocation

        job = _job()
        job.location = JobLocation()  # no raw, no geo
        result = evaluate(job)
        assert result.quality.geocode_pending is False

    def test_evaluate_returns_same_job_object(self):
        job = _job()
        result = evaluate(job)
        assert result is job


# ---------------------------------------------------------------------------
# Ground truth — fixtures with sufficient classifications must reach ACTIVE.
# ---------------------------------------------------------------------------


class TestQualityGateGroundTruth:
    def test_valid_and_premium_fixtures_pass_gate(self, ground_truth_pass):
        """All fixtures expected valid/premium must pass the new strict gate."""
        failures: list[str] = []

        for f in ground_truth_pass:
            ai_out = f["expected_output"].get("ai_output")
            if ai_out is None:
                continue
            inp = f["input"]

            cl = JobClassification(
                technical_skills=ai_out.get("skills", []),
                seniority=ai_out.get("seniority", "unknown"),
                role_family=ai_out.get("role_family", "other"),
                remote_mode=ai_out.get("remote_mode", "unknown"),
                employment_type=ai_out.get("employment_type", "full_time"),
                salary_min=ai_out.get("salary_min"),
                salary_max=ai_out.get("salary_max"),
                ai_confidence=ai_out.get("confidence", 0.0),
                quality_flags=ai_out.get("quality_flags", []),
            )
            ok, reasons = passes_quality_gate(
                cl,
                company_name=inp["company_name"],
                description=inp["description"],
            )
            if not ok:
                failures.append(f"{inp['title'][:40]}: gate rejected with {reasons}")

        assert not failures, "Expected-valid fixtures failed gate:\n" + "\n".join(failures)
