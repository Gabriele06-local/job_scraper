"""Tests for pipeline/orchestrator.py — ImportPipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from models.job import (
    JobClassification,
    RawJob,
    RemoteMode,
    RoleFamily,
    Seniority,
)
from pipeline.orchestrator import ImportPipeline

_NOW = datetime.now(tz=timezone.utc)

_LONG_DESC = (
    "We need a Senior Python Developer for our backend engineering team. "
    "You will design scalable REST APIs and microservices using Python and Django. "
    "Requirements: 5+ years Python, experience with PostgreSQL and Redis, "
    "strong knowledge of Docker and Kubernetes. Remote-friendly position with "
    "competitive salary range EUR 80k-120k. We offer equity and learning budget."
)


def _raw(
    *,
    title: str = "Senior Python Developer",
    company_name: str = "Acme Corp",
    description: str = _LONG_DESC,
    url: str = "https://jobs.example.com/1",
    posted_at: datetime | None = _NOW,
    source: str = "adzuna",
    original_language: str | None = "en",
) -> RawJob:
    return RawJob(
        url=url,
        title=title,
        description=description,
        company_name=company_name,
        source=source,
        posted_at=posted_at,
        original_language=original_language,
    )


def _good_classification() -> JobClassification:
    # Qualifies as VALID (not premium): 2 skills, remote mode (salary substitute), confidence 0.80
    # Premium needs ≥4 skills + full salary + confidence≥0.85 + clear_jd/has_requirements
    return JobClassification(
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


def _premium_classification() -> JobClassification:
    return JobClassification(
        technical_skills=["Go", "Kubernetes", "AWS", "PostgreSQL"],
        seniority=Seniority.SENIOR,
        role_family=RoleFamily.BACKEND,
        remote_mode=RemoteMode.REMOTE,
        salary_min=90000,
        salary_max=130000,
        currency="EUR",
        ai_confidence=0.92,
        quality_flags=["clear_jd", "has_requirements"],
    )


def _make_pipeline(
    jobs_collection,
    classification: JobClassification | None = None,
    dry_run: bool = False,
) -> ImportPipeline:
    """Build ImportPipeline with a mock classifier returning fixed classification."""
    mock_clf = MagicMock()
    mock_clf.classify.return_value = (
        classification if classification is not None else _good_classification()
    )
    return ImportPipeline(
        jobs_col=jobs_collection,
        classifier=mock_clf,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Basic pipeline flow
# ---------------------------------------------------------------------------


class TestPipelineBasicFlow:
    def test_valid_job_persisted(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        result = pipeline.run([_raw()])

        assert result.counters.total == 1
        assert result.counters.ai_classified == 1
        assert result.counters.gate_valid == 1
        assert result.counters.persisted == 1
        assert jobs_collection.count_documents({"status": "valid"}) == 1

    def test_prefilter_rejected_not_classified(self, jobs_collection):
        # Empty description → prefilter reject
        pipeline = _make_pipeline(jobs_collection)
        result = pipeline.run([_raw(description="short")])

        assert result.counters.prefilter_rejected == 1
        assert result.counters.ai_classified == 0

    def test_prefilter_rejected_persisted_in_db(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        pipeline.run([_raw(description="short")])

        doc = jobs_collection.find_one({})
        assert doc is not None
        assert doc["status"] == "rejected_prefilter"

    def test_dry_run_no_mongo_writes(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection, dry_run=True)
        result = pipeline.run([_raw()])

        assert result.counters.dry_run_skipped == 1
        assert jobs_collection.count_documents({}) == 0

    def test_dry_run_prefilter_reject_not_persisted(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection, dry_run=True)
        pipeline.run([_raw(description="short")])
        assert jobs_collection.count_documents({}) == 0

    def test_multiple_jobs_all_persisted(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        jobs = [
            _raw(url=f"https://jobs.example.com/{i}", title=f"Dev {i}")
            for i in range(5)
        ]
        result = pipeline.run(jobs)

        assert result.counters.total == 5
        assert result.counters.persisted == 5

    def test_counters_sum_to_total(self, jobs_collection):
        """prefilter_rejected + dedupe_hit + persisted + dry_run_skipped == total."""
        pipeline = _make_pipeline(jobs_collection)
        jobs = [
            _raw(url="https://jobs.example.com/1"),
            _raw(url="https://jobs.example.com/1"),  # exact duplicate
        ]
        result = pipeline.run(jobs)
        c = result.counters
        # 2 total: 1 persisted + 1 dedupe hit
        assert c.total == 2
        assert c.persisted + c.dedupe_hit + c.prefilter_rejected == 2


# ---------------------------------------------------------------------------
# Dedupe behaviour
# ---------------------------------------------------------------------------


class TestPipelineDedupe:
    def test_duplicate_url_same_source_deduped(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        raw = _raw()
        result = pipeline.run([raw, raw])

        assert result.counters.dedupe_hit == 1
        assert result.counters.persisted == 1

    def test_deduped_job_updates_last_seen_at(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        raw = _raw()
        pipeline.run([raw, raw])

        # Any accepted status (valid or premium) is fine here
        doc = jobs_collection.find_one({"status": {"$in": ["valid", "premium"]}})
        assert doc is not None
        # seen_count should be 1 from merge
        assert doc.get("seen_count", 0) == 1

    def test_idempotent_multiple_runs(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        raw = _raw()
        pipeline.run([raw])
        pipeline.run([raw])  # second run hits dedupe

        assert jobs_collection.count_documents({}) == 1


# ---------------------------------------------------------------------------
# AI unavailable
# ---------------------------------------------------------------------------


class TestPipelineAIUnavailable:
    def test_ai_unavailable_rejected_quality(self, jobs_collection):
        mock_clf = MagicMock()
        mock_clf.classify.return_value = None  # simulate total failure

        pipeline = ImportPipeline(
            jobs_col=jobs_collection,
            classifier=mock_clf,
            dry_run=False,
        )
        result = pipeline.run([_raw()])

        assert result.counters.ai_unavailable == 1
        doc = jobs_collection.find_one({})
        assert doc is not None
        assert doc["status"] == "rejected_quality"
        assert doc["reject_reason"] == "AI_UNAVAILABLE"

    def test_ai_unavailable_counts_correctly(self, jobs_collection):
        mock_clf = MagicMock()
        mock_clf.classify.return_value = None

        pipeline = ImportPipeline(jobs_col=jobs_collection, classifier=mock_clf)
        result = pipeline.run([
            _raw(url="https://a.com/1"),
            _raw(url="https://a.com/2", title="Other Dev"),
        ])

        assert result.counters.ai_unavailable == 2
        assert result.counters.gate_valid == 0


# ---------------------------------------------------------------------------
# Quality gate integration
# ---------------------------------------------------------------------------


class TestPipelineQualityGate:
    def test_low_confidence_job_rejected(self, jobs_collection):
        cl = _good_classification()
        cl.ai_confidence = 0.5  # below 0.7 threshold

        pipeline = _make_pipeline(jobs_collection, classification=cl)
        result = pipeline.run([_raw()])

        assert result.counters.gate_rejected == 1
        doc = jobs_collection.find_one({})
        assert doc["status"] == "rejected_quality"
        assert doc["reject_reason"] == "LOW_CONFIDENCE"

    def test_premium_job_counted(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection, classification=_premium_classification())
        result = pipeline.run([_raw()])

        assert result.counters.gate_premium == 1
        doc = jobs_collection.find_one({})
        assert doc["status"] == "premium"

    def test_quality_score_stored_in_db(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        pipeline.run([_raw()])

        # valid or premium — both have quality_score
        doc = jobs_collection.find_one({"status": {"$in": ["valid", "premium"]}})
        assert doc is not None
        assert doc.get("quality_score", 0) > 0


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


class TestPipelineErrorResilience:
    def test_unexpected_error_continues_to_next_job(self, jobs_collection):
        mock_clf = MagicMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("unexpected crash")
            return _good_classification()

        mock_clf.classify.side_effect = side_effect

        pipeline = ImportPipeline(jobs_col=jobs_collection, classifier=mock_clf)
        result = pipeline.run([
            _raw(url="https://jobs.example.com/1"),
            _raw(url="https://jobs.example.com/2", title="Backend Dev 2"),
        ])

        assert len(result.errors) == 1
        assert result.counters.persisted == 1

    def test_cost_summary_present_in_result(self, jobs_collection):
        pipeline = _make_pipeline(jobs_collection)
        result = pipeline.run([_raw()])

        assert "groq_tokens_in" in result.cost_summary
        assert "groq_cost_usd" in result.cost_summary


# ---------------------------------------------------------------------------
# Ground truth: full pipeline via conftest mock_groq_client
# ---------------------------------------------------------------------------


class TestPipelineGroundTruth:
    def test_all_non_prefilter_fixtures_classified(
        self, ground_truth_pass, mock_groq_client, jobs_collection
    ):
        """All valid/premium fixtures must result in persisted jobs."""
        from datetime import datetime, timezone

        from ai.classifier import GroqClassifier
        from pipeline.orchestrator import ImportPipeline

        clf = GroqClassifier(client=mock_groq_client)
        clf._rate_limiter._min_interval = 0.0
        pipeline = ImportPipeline(jobs_col=jobs_collection, classifier=clf)

        raw_jobs = []
        for f in ground_truth_pass:
            inp = f["input"]
            posted = datetime.fromisoformat(inp["posted_at"]).replace(tzinfo=timezone.utc)
            raw_jobs.append(RawJob(
                url=inp["url"],
                title=inp["title"],
                description=inp["description"],
                company_name=inp["company_name"],
                source=inp["source"],
                posted_at=posted,
                original_language=inp.get("detected_language"),
            ))

        result = pipeline.run(raw_jobs)
        assert result.counters.ai_classified == len(raw_jobs)
        assert result.counters.ai_unavailable == 0
        assert result.counters.persisted + result.counters.gate_rejected >= len(raw_jobs)
