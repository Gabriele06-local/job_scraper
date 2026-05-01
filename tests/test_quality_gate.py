"""Tests for pipeline/quality_gate.py."""

from __future__ import annotations

from datetime import datetime, timezone

from models.job import (
    Job,
    JobClassification,
    JobCompany,
    JobContent,
    JobSource,
    JobStatus,
    Language,
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

_LONG_DESC = (
    "We are looking for a Senior Python Developer to join our backend team. "
    "You will design REST APIs with Django and PostgreSQL. "
    "Requirements: 5+ years Python, Docker, Kubernetes. Remote, EUR 80k-120k."
)


def _cls(**kwargs) -> JobClassification:
    # Defaults qualify as VALID (not premium): 2 skills, no salary, remote mode, confidence=0.80
    # Premium requires: ≥4 skills + full salary + confidence≥0.85 + clear_jd/has_requirements flag
    defaults = dict(
        technical_skills=["Python", "Django"],
        seniority=Seniority.SENIOR,
        role_family=RoleFamily.BACKEND,
        remote_mode=RemoteMode.REMOTE,
        salary_min=None,
        salary_max=None,
        currency=None,
        ai_confidence=0.80,
        quality_flags=[],
    )
    defaults.update(kwargs)
    return JobClassification(**defaults)


def _job(classification: JobClassification | None = None) -> Job:
    cl = classification or _cls()
    return Job(
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


# ---------------------------------------------------------------------------
# compute_quality_score
# ---------------------------------------------------------------------------


class TestComputeQualityScore:
    def test_perfect_score(self):
        cl = _cls(
            technical_skills=["A", "B", "C", "D", "E"],
            seniority=Seniority.SENIOR,
            salary_min=80000,
            salary_max=120000,
            remote_mode=RemoteMode.REMOTE,
            ai_confidence=1.0,
        )
        score = compute_quality_score(cl)
        assert score == 100

    def test_zero_score(self):
        cl = _cls(
            technical_skills=[],
            seniority=Seniority.UNKNOWN,
            salary_min=None,
            salary_max=None,
            remote_mode=RemoteMode.UNKNOWN,
            ai_confidence=0.0,
        )
        assert compute_quality_score(cl) == 0

    def test_skills_capped_at_5(self):
        """6 skills should score same as 5 — capped."""
        base = dict(salary_min=None, salary_max=None,
                    remote_mode=RemoteMode.UNKNOWN, ai_confidence=0.0,
                    seniority=Seniority.UNKNOWN)
        cl5 = _cls(technical_skills=["A", "B", "C", "D", "E"], **base)
        cl6 = _cls(technical_skills=["A", "B", "C", "D", "E", "F"], **base)
        assert compute_quality_score(cl5) == compute_quality_score(cl6)

    def test_partial_salary(self):
        """Only salary_min set → salary_score=0.6."""
        cl = _cls(salary_min=50000, salary_max=None,
                  technical_skills=[], seniority=Seniority.UNKNOWN,
                  remote_mode=RemoteMode.UNKNOWN, ai_confidence=0.0)
        score = compute_quality_score(cl)
        # 0.20 * 0.6 = 12
        assert score == 12

    def test_no_salary(self):
        cl = _cls(salary_min=None, salary_max=None,
                  technical_skills=[], seniority=Seniority.UNKNOWN,
                  remote_mode=RemoteMode.UNKNOWN, ai_confidence=0.0)
        assert compute_quality_score(cl) == 0

    def test_remote_scores_higher_than_onsite(self):
        base = dict(technical_skills=[], seniority=Seniority.UNKNOWN,
                    salary_min=None, salary_max=None, ai_confidence=0.0)
        remote_score = compute_quality_score(_cls(**base, remote_mode=RemoteMode.REMOTE))
        onsite_score = compute_quality_score(_cls(**base, remote_mode=RemoteMode.ONSITE))
        assert remote_score > onsite_score

    def test_returns_int(self):
        assert isinstance(compute_quality_score(_cls()), int)

    def test_range_0_100(self):
        for conf in [0.0, 0.5, 1.0]:
            score = compute_quality_score(_cls(ai_confidence=conf))
            assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# passes_quality_gate
# ---------------------------------------------------------------------------


class TestPassesQualityGate:
    def test_valid_classification_passes(self):
        ok, reasons = passes_quality_gate(_cls())
        assert ok is True
        assert reasons == []

    def test_insufficient_skills_first_match(self):
        # insufficient_skills wins over unknown_seniority (first-match order)
        cl = _cls(technical_skills=["A"], seniority=Seniority.UNKNOWN)
        ok, reasons = passes_quality_gate(cl)
        assert not ok
        assert reasons == [QualityRejectReason.INSUFFICIENT_SKILLS.value]

    def test_unknown_seniority(self):
        cl = _cls(seniority=Seniority.UNKNOWN)
        ok, reasons = passes_quality_gate(cl)
        assert not ok
        assert reasons == [QualityRejectReason.UNKNOWN_SENIORITY.value]

    def test_unknown_role_family(self):
        cl = _cls(role_family=RoleFamily.OTHER)
        ok, reasons = passes_quality_gate(cl)
        assert not ok
        assert reasons == [QualityRejectReason.UNKNOWN_ROLE_FAMILY.value]

    def test_no_salary_no_remote_rejected(self):
        cl = _cls(salary_min=None, salary_max=None, remote_mode=RemoteMode.ONSITE)
        ok, reasons = passes_quality_gate(cl)
        assert not ok
        assert reasons == [QualityRejectReason.NO_SALARY_AND_NO_REMOTE_MODE.value]

    def test_no_salary_but_hybrid_passes(self):
        cl = _cls(salary_min=None, salary_max=None, remote_mode=RemoteMode.HYBRID)
        ok, _ = passes_quality_gate(cl)
        assert ok is True

    def test_no_salary_but_remote_passes(self):
        cl = _cls(salary_min=None, salary_max=None, remote_mode=RemoteMode.REMOTE)
        ok, _ = passes_quality_gate(cl)
        assert ok is True

    def test_low_confidence_rejected(self):
        cl = _cls(ai_confidence=0.6)
        ok, reasons = passes_quality_gate(cl)
        assert not ok
        assert reasons == [QualityRejectReason.LOW_CONFIDENCE.value]

    def test_custom_threshold(self):
        cl = _cls(ai_confidence=0.75)
        # Passes at default 0.7
        ok, _ = passes_quality_gate(cl, threshold=0.7)
        assert ok is True
        # Fails at 0.80
        ok2, reasons2 = passes_quality_gate(cl, threshold=0.80)
        assert not ok2
        assert reasons2 == [QualityRejectReason.LOW_CONFIDENCE.value]

    def test_exactly_2_skills_passes(self):
        cl = _cls(technical_skills=["A", "B"])
        ok, _ = passes_quality_gate(cl)
        assert ok is True

    def test_one_skill_fails(self):
        cl = _cls(technical_skills=["A"])
        ok, _ = passes_quality_gate(cl)
        assert not ok

    def test_zero_skills_fails(self):
        cl = _cls(technical_skills=[])
        ok, _ = passes_quality_gate(cl)
        assert not ok

    def test_exactly_at_threshold_passes(self):
        cl = _cls(ai_confidence=0.7)
        ok, _ = passes_quality_gate(cl, threshold=0.7)
        assert ok is True

    def test_just_below_threshold_fails(self):
        cl = _cls(ai_confidence=0.699)
        ok, _ = passes_quality_gate(cl, threshold=0.7)
        assert not ok


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

    def test_missing_salary_min_not_premium(self):
        cl = _cls(technical_skills=["A", "B", "C", "D"], salary_min=None, salary_max=120000)
        assert is_premium(cl) is False

    def test_missing_salary_max_not_premium(self):
        cl = _cls(technical_skills=["A", "B", "C", "D"], salary_min=80000, salary_max=None)
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

    def test_missing_quality_flag_not_premium(self):
        cl = _cls(
            technical_skills=["A", "B", "C", "D"],
            ai_confidence=0.90,
            quality_flags=["has_tech_stack"],  # no clear_jd or has_requirements
        )
        assert is_premium(cl) is False

    def test_has_requirements_flag_qualifies_for_premium(self):
        cl = _cls(
            technical_skills=["A", "B", "C", "D"],
            salary_min=80000,
            salary_max=120000,
            ai_confidence=0.90,
            quality_flags=["has_requirements"],
        )
        assert is_premium(cl) is True


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_valid_job_gets_valid_status(self):
        job = _job()
        result = evaluate(job)
        assert result.status == JobStatus.VALID
        assert result.reject_reason is None
        assert result.quality.quality_score > 0

    def test_premium_job_gets_premium_status(self):
        cl = _cls(
            technical_skills=["Go", "Kubernetes", "AWS", "PostgreSQL"],
            salary_min=90000,
            salary_max=130000,
            ai_confidence=0.92,
            quality_flags=["clear_jd", "has_requirements"],
        )
        job = _job(cl)
        result = evaluate(job)
        assert result.status == JobStatus.PREMIUM

    def test_rejected_job_has_reason(self):
        cl = _cls(technical_skills=[])  # INSUFFICIENT_SKILLS
        job = _job(cl)
        result = evaluate(job)
        assert result.status == JobStatus.REJECTED_QUALITY
        assert result.reject_reason == QualityRejectReason.INSUFFICIENT_SKILLS.value

    def test_quality_score_set_on_rejected(self):
        cl = _cls(technical_skills=[])
        job = _job(cl)
        result = evaluate(job)
        # Score is set (>= 0); other components (remote, confidence) contribute even with 0 skills
        assert 0.0 <= result.quality.quality_score <= 1.0

    def test_quality_score_set_on_valid(self):
        job = _job()
        result = evaluate(job)
        assert 0.0 < result.quality.quality_score <= 1.0

    def test_evaluate_returns_same_job_object(self):
        job = _job()
        result = evaluate(job)
        assert result is job  # in-place modification

    def test_unknown_seniority_reject_reason(self):
        cl = _cls(seniority=Seniority.UNKNOWN)
        job = _job(cl)
        result = evaluate(job)
        assert result.reject_reason == QualityRejectReason.UNKNOWN_SENIORITY.value

    def test_low_confidence_reject_reason(self):
        cl = _cls(ai_confidence=0.5)
        job = _job(cl)
        result = evaluate(job)
        assert result.reject_reason == QualityRejectReason.LOW_CONFIDENCE.value


# ---------------------------------------------------------------------------
# Ground truth: quality gate on all fixtures (mocked AI)
# ---------------------------------------------------------------------------


class TestQualityGateGroundTruth:
    def test_valid_and_premium_fixtures_pass_gate(
        self, ground_truth_pass
    ):
        """All fixtures expected valid/premium must pass the gate."""
        failures: list[str] = []

        for f in ground_truth_pass:
            ai_out = f["expected_output"].get("ai_output")
            if ai_out is None:
                continue

            cl = JobClassification(
                technical_skills=ai_out.get("skills", []),
                seniority=ai_out.get("seniority", "unknown"),
                role_family=ai_out.get("role_family", "other"),
                remote_mode=ai_out.get("remote_mode", "unknown"),
                salary_min=ai_out.get("salary_min"),
                salary_max=ai_out.get("salary_max"),
                ai_confidence=ai_out.get("confidence", 0.0),
                quality_flags=ai_out.get("quality_flags", []),
            )
            ok, reasons = passes_quality_gate(cl)
            if not ok:
                failures.append(
                    f"{f['input']['title'][:40]}: gate rejected with {reasons}"
                )

        assert not failures, "Expected-valid fixtures failed gate:\n" + "\n".join(failures)

    def test_quality_bad_fixtures_fail_gate(
        self, ground_truth_reject_quality
    ):
        """Fixtures expected rejected_quality must fail the gate."""
        failures: list[str] = []

        for f in ground_truth_reject_quality:
            ai_out = f["expected_output"].get("ai_output")
            if ai_out is None:
                continue

            cl = JobClassification(
                technical_skills=ai_out.get("skills", []),
                seniority=ai_out.get("seniority", "unknown"),
                role_family=ai_out.get("role_family", "other"),
                remote_mode=ai_out.get("remote_mode", "unknown"),
                salary_min=ai_out.get("salary_min"),
                salary_max=ai_out.get("salary_max"),
                ai_confidence=ai_out.get("confidence", 0.0),
                quality_flags=ai_out.get("quality_flags", []),
            )
            ok, _ = passes_quality_gate(cl)
            if ok:
                failures.append(
                    f"{f['input']['title'][:40]}: expected gate reject but passed"
                )

        assert not failures, "Expected-reject fixtures passed gate:\n" + "\n".join(failures)

    def test_expected_status_matches_evaluate(
        self, ground_truth_all
    ):
        """evaluate() status must match expected_status for non-prefilter fixtures."""
        from models.job import JobCompany, JobContent, JobSource, Language
        from pipeline.quality_gate import evaluate

        mismatches: list[str] = []
        _NOW_DT = datetime.now(tz=timezone.utc)

        for f in ground_truth_all:
            expected = f["expected_output"]["expected_status"]
            if expected == "rejected_prefilter":
                continue  # Quality gate doesn't apply to prefilter rejects

            ai_out = f["expected_output"].get("ai_output")
            if ai_out is None:
                continue

            cl = JobClassification(
                technical_skills=ai_out.get("skills", []),
                seniority=ai_out.get("seniority", "unknown"),
                role_family=ai_out.get("role_family", "other"),
                remote_mode=ai_out.get("remote_mode", "unknown"),
                salary_min=ai_out.get("salary_min"),
                salary_max=ai_out.get("salary_max"),
                ai_confidence=ai_out.get("confidence", 0.0),
                quality_flags=ai_out.get("quality_flags", []),
            )
            inp = f["input"]
            job = Job(
                url=inp["url"],
                dedup_hash="test_hash",
                source_info=JobSource(source=inp["source"]),
                content=JobContent(
                    title=inp["title"],
                    title_normalized=inp["title"].lower(),
                    description=inp["description"],
                    language=Language.EN,
                ),
                company=JobCompany(
                    name=inp["company_name"],
                    name_normalized=inp["company_name"].lower(),
                ),
                posted_at=_NOW_DT,
                classification=cl,
            )

            result = evaluate(job)
            actual = result.status.value

            if actual != expected:
                mismatches.append(
                    f"{inp['title'][:40]}: expected={expected}, got={actual} "
                    f"(reason={result.reject_reason})"
                )

        assert not mismatches, "Status mismatches:\n" + "\n".join(mismatches)
