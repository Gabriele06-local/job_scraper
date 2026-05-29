"""Tests for pipeline/company_scorer.py — CompanyTrustScorer."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from models.job import (
    Job,
    JobClassification,
    JobCompany,
    JobContent,
    JobLocation,
    JobQuality,
    JobSalary,
    JobSource,
    JobStatus,
    Language,
    QualityTier,
    Seniority,
)
from pipeline.company_scorer import (
    CompanyTrustScorer,
    ScrapeMetrics,
    compute_trust_score,
)


def _make_job(
    company_name: str = "Acme Corp",
    description: str = "x" * 1200,
    seniority: str = "senior",
    is_premium: bool = False,
    has_salary: bool = True,
    has_skills: bool = True,
) -> Job:
    sal = JobSalary(min=50_000, max=120_000) if has_salary else JobSalary()
    skills = ["python", "docker"] if has_skills else []
    q_tier = QualityTier.PREMIUM if is_premium else QualityTier.VALID
    return Job(
        url="https://example.com/1",
        dedup_hash="abc",
        cross_source_hash="def",
        source_info=JobSource(source="adzuna"),
        content=JobContent(
            title="Test",
            title_normalized="test",
            description=description,
            language=Language.EN,
        ),
        company=JobCompany(
            name=company_name,
            name_normalized=company_name.lower().strip(),
        ),
        location=JobLocation(),
        salary=sal,
        posted_at=datetime.now(tz=timezone.utc),
        first_seen_at=datetime.now(tz=timezone.utc),
        last_seen_at=datetime.now(tz=timezone.utc),
        classification=JobClassification(
            technical_skills=skills,
            seniority=Seniority(seniority),
        ),
        quality=JobQuality(quality_score=80.0, quality_tier=q_tier),
        status=JobStatus.ACTIVE,
    )


class TestComputeTrustScore:
    def test_default_on_empty_metrics(self):
        score = compute_trust_score(ScrapeMetrics())
        assert score == 80.0

    def test_perfect_score(self):
        metrics = ScrapeMetrics(
            total_seen=10,
            total_passed=10,
            quality_score_sum=1000.0,
            with_salary=10,
            with_skills=10,
            premium_count=10,
            with_seniority=10,
            description_len_sum=20000,
        )
        score = compute_trust_score(metrics)
        assert score == 100.0

    def test_poor_score(self):
        metrics = ScrapeMetrics(
            total_seen=10,
            total_passed=0,
            quality_score_sum=100.0,
            with_salary=0,
            with_skills=0,
            premium_count=0,
            with_seniority=0,
            description_len_sum=500,
        )
        score = compute_trust_score(metrics)
        assert score < 40.0

    def test_mid_range(self):
        metrics = ScrapeMetrics(
            total_seen=20,
            total_passed=10,
            quality_score_sum=1200.0,
            with_salary=10,
            with_skills=8,
            premium_count=3,
            with_seniority=12,
            description_len_sum=16000,
        )
        score = compute_trust_score(metrics)
        assert 30.0 <= score <= 90.0

    def test_clamps_to_0_100(self):
        metrics = ScrapeMetrics(
            total_seen=1,
            total_passed=0,
            quality_score_sum=0.0,
            with_salary=0,
            with_skills=0,
            premium_count=0,
            with_seniority=0,
            description_len_sum=10,
        )
        score = compute_trust_score(metrics)
        assert 0.0 <= score <= 100.0


class TestCompanyTrustScorer:
    def test_record_updates_metrics(self):
        scorer = CompanyTrustScorer()
        job = _make_job(company_name="Acme Corp")

        scorer.record("acme corp", job, passed=True, quality_score=80.0)

        assert scorer.company_count == 1
        metrics = scorer._current["acme corp"]
        assert metrics.total_seen == 1
        assert metrics.total_passed == 1
        assert metrics.with_salary == 1
        assert metrics.with_skills == 1
        assert metrics.premium_count == 0
        assert metrics.with_seniority == 1

    def test_record_multiple_jobs_same_company(self):
        scorer = CompanyTrustScorer()
        good = _make_job(company_name="Acme Corp")
        bad = _make_job(
            company_name="Acme Corp", is_premium=False, has_salary=False,
            has_skills=False, seniority="unknown",
        )

        scorer.record("acme corp", good, passed=True, quality_score=90.0)
        scorer.record("acme corp", bad, passed=False, quality_score=30.0)

        metrics = scorer._current["acme corp"]
        assert metrics.total_seen == 2
        assert metrics.total_passed == 1
        assert metrics.with_salary == 1
        assert metrics.with_skills == 1
        assert metrics.premium_count == 0
        assert metrics.with_seniority == 1

    def test_record_multiple_companies(self):
        scorer = CompanyTrustScorer()
        scorer.record("acme corp", _make_job("Acme Corp"), passed=True, quality_score=80.0)
        scorer.record("other inc", _make_job("Other Inc"), passed=True, quality_score=70.0)

        assert scorer.company_count == 2

    def test_compute_returns_score_for_seen_company(self):
        scorer = CompanyTrustScorer()
        scorer.record("acme corp", _make_job("Acme Corp"), passed=True, quality_score=80.0)

        score = scorer.compute("acme corp")
        assert 0.0 <= score <= 100.0

    def test_compute_returns_default_for_unseen_company(self):
        scorer = CompanyTrustScorer()
        score = scorer.compute("unknown-co")
        assert score == 80.0

    def test_persist_all_writes_to_collection(self):
        scorer = CompanyTrustScorer()
        scorer.record("acme corp", _make_job("Acme Corp"), passed=True, quality_score=80.0)

        coll = MagicMock()
        coll.find_one.return_value = None  # no existing document

        scorer.persist_all(coll)

        assert coll.update_one.call_count == 1
        args, _ = coll.update_one.call_args
        assert args[0] == {"name_normalized": "acme corp"}
        assert args[1]["$set"]["trustScore"] > 0

    def test_persist_all_blends_with_existing(self):
        scorer = CompanyTrustScorer()
        scorer.record("acme corp", _make_job("Acme Corp"), passed=True, quality_score=80.0)

        coll = MagicMock()
        coll.find_one.return_value = {
            "name_normalized": "acme corp",
            "scrape_metrics": {
                "total_jobs_seen": 50,
                "total_jobs_passed": 40,
                "sum_quality_scores": 3500.0,
                "jobs_with_salary": 30,
                "jobs_with_skills": 35,
                "premium_count": 10,
                "jobs_with_seniority": 25,
                "sum_description_length": 50000,
            },
        }

        scorer.persist_all(coll)

        assert coll.update_one.call_count == 1
        args, _ = coll.update_one.call_args
        inc_fields = args[1]["$inc"]
        assert inc_fields["scrape_metrics.total_jobs_seen"] == 1
        assert inc_fields["scrape_metrics.sum_quality_scores"] == 80.0

    def test_no_persist_if_no_companies(self):
        scorer = CompanyTrustScorer()
        coll = MagicMock()
        scorer.persist_all(coll)
        coll.update_one.assert_not_called()
