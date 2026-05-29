"""Tests for pipeline/orchestrator.py — all pipeline stages."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import mongomock
import pytest
from bson import ObjectId

from models.job import (
    Category,
    EmploymentType,
    JobClassification,
    JobStatus,
    RawJob,
    RemoteMode,
    RoleFamily,
    Seniority,
    compute_dedup_hash,
)
from pipeline.orchestrator import ImportPipeline


@pytest.fixture
def jobs():
    c = mongomock.MongoClient()
    col = c["testdb"]["jobs"]
    col.create_index("dedup_hash", sparse=True, unique=True)
    col.create_index("cross_source_hash", sparse=True)
    col.create_index("company.name_normalized")
    return col


@pytest.fixture
def companies():
    c = mongomock.MongoClient()
    return c["testdb"]["companies"]


_NOW = datetime.now(tz=timezone.utc)


def _raw(**overrides) -> RawJob:
    data = dict(
        url="https://example.com/job",
        title="Software Engineer",
        description="We need a software engineer with Python and Django experience. " * 10,
        company_name="Acme Corp",
        source="test_source",
        posted_at=_NOW,
    )
    data.update(overrides)
    return RawJob(**data)


def _make_classifier(result: JobClassification | None = None):
    """Return a mock classifier that returns the given result."""
    m = MagicMock()
    if result is not None:
        m.classify.return_value = result
    else:
        m.classify.return_value = None
    return m


_VALID_CLASSIFICATION = JobClassification(
    technical_skills=["Python", "Django"],
    skills=["Python", "Django"],
    category=Category.SOFTWARE_ENGINEERING,
    role_family=RoleFamily.BACKEND,
    seniority=Seniority.SENIOR,
    remote_mode=RemoteMode.REMOTE,
    employment_type=EmploymentType.FULL_TIME,
    salary_min=80_000,
    salary_max=120_000,
    currency="EUR",
    ai_confidence=0.92,
    quality_flags=["clear_jd", "has_requirements"],
    cv_drop_score=0.1,
)

_LOW_QUALITY_CLASSIFICATION = JobClassification(
    skills=[],
    category=Category.SOFTWARE_ENGINEERING,
    role_family=RoleFamily.OTHER,
    seniority=Seniority.UNKNOWN,
    remote_mode=RemoteMode.UNKNOWN,
    employment_type=EmploymentType.UNKNOWN,
    ai_confidence=0.1,
)


def pipeline(jobs, companies, **kwargs) -> ImportPipeline:
    return ImportPipeline(jobs_col=jobs, companies_col=companies, **kwargs)


# ---------------------------------------------------------------------------
# Stage 1: Pre-filter rejection
# ---------------------------------------------------------------------------


def test_prefilter_rejects_short_description(jobs, companies):
    raw = _raw(description="Too short")
    p = pipeline(jobs, companies, classifier=_make_classifier())
    result = p.run([raw])
    assert result.counters.prefilter_rejected == 1
    assert result.counters.persisted == 0


def test_prefilter_persists_rejected(jobs, companies):
    raw = _raw(description="Short")
    p = pipeline(jobs, companies, classifier=_make_classifier())
    result = p.run([raw])
    assert result.counters.prefilter_rejected == 1
    stored = jobs.find_one({"source": "test_source"})
    assert stored is not None
    assert stored["status"] == JobStatus.REJECTED_PREFILTER.value


# ---------------------------------------------------------------------------
# Stage 0.5: URL invalid
# ---------------------------------------------------------------------------


def test_url_invalid_rejected(jobs, companies):
    raw = _raw()
    p = pipeline(jobs, companies, classifier=_make_classifier())
    result = p.run([raw], url_results={raw.url: _url_result(False, "NOT_FOUND")})
    assert result.counters.url_invalid == 1
    assert result.counters.persisted == 0


def test_url_invalid_persists_as_expired(jobs, companies):
    raw = _raw()
    p = pipeline(jobs, companies, classifier=_make_classifier())
    p.run([raw], url_results={raw.url: _url_result(False, "NOT_FOUND")})
    stored = jobs.find_one({"source": "test_source"})
    assert stored is not None
    assert stored["status"] == JobStatus.EXPIRED.value
    assert "URL_INVALID" in (stored.get("reject_reason") or "")


# ---------------------------------------------------------------------------
# Stage 2: Hash dedup
# ---------------------------------------------------------------------------


def test_dedupe_hit_skips_ai(jobs, companies):
    raw = _raw()
    dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
    jobs.insert_one({"url": "https://old.url/job", "dedup_hash": dedup_hash})
    classifier = _make_classifier(_VALID_CLASSIFICATION)
    p = pipeline(jobs, companies, classifier=classifier)
    result = p.run([raw])
    assert result.counters.dedupe_hit == 1
    assert result.counters.ai_classified == 0
    classifier.classify.assert_not_called()


def test_dedupe_hit_increments_seen_count(jobs, companies):
    raw = _raw()
    dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
    jobs.insert_one(
        {
            "url": "https://old.url/job",
            "dedup_hash": dedup_hash,
            "source": "test_source",
        }
    )
    p = pipeline(jobs, companies, classifier=_make_classifier())
    p.run([raw])
    stored = jobs.find_one({"dedup_hash": dedup_hash})
    assert stored is not None
    assert stored.get("seen_count", 0) == 1


# ---------------------------------------------------------------------------
# Stage 2a: Cross-source dedup
# ---------------------------------------------------------------------------


def test_cross_source_hit(jobs, companies):
    raw = _raw(source="source_b", title="Senior Backend Engineer")
    _insert_job(
        jobs,
        url="https://old.url/job",
        title="Senior Backend Engineer",
        company_name="Acme Corp",
        source="source_a",
        description="short",
    )
    classifier = _make_classifier(_VALID_CLASSIFICATION)
    p = pipeline(jobs, companies, classifier=classifier)
    result = p.run([raw])
    assert result.counters.cross_source_hit == 1
    assert result.counters.ai_classified == 0
    classifier.classify.assert_not_called()


# ---------------------------------------------------------------------------
# Stage 2b: Fuzzy dedup
# ---------------------------------------------------------------------------


def test_fuzzy_dedup_hit(jobs, companies):
    _insert_job(
        jobs,
        url="https://old.url/job",
        title="Software Engineer - Backend",
        company_name="Acme Corp",
        source="source_a",
    )
    raw = _raw(source="source_b", title="Software Engineer Backend")
    classifier = _make_classifier(_VALID_CLASSIFICATION)
    p = pipeline(jobs, companies, classifier=classifier)
    result = p.run([raw])
    assert result.counters.fuzzy_dup_flagged == 1
    assert result.counters.ai_classified == 0


# ---------------------------------------------------------------------------
# Stage 3 + 4: AI classify + quality gate
# ---------------------------------------------------------------------------


def test_ai_classify_and_pass(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(_VALID_CLASSIFICATION))
    result = p.run([_raw()])
    assert result.counters.ai_classified == 1
    assert result.counters.gate_valid >= 1 or result.counters.gate_premium >= 1
    assert result.counters.persisted == 1


def test_ai_classify_and_reject(jobs, companies):
    p = pipeline(
        jobs, companies, classifier=_make_classifier(_LOW_QUALITY_CLASSIFICATION)
    )
    result = p.run([_raw()])
    assert result.counters.ai_classified == 1
    assert result.counters.gate_rejected == 1
    assert result.counters.persisted == 1


def test_ai_unavailable(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(None))
    result = p.run([_raw()])
    assert result.counters.ai_unavailable == 1
    assert result.counters.persisted == 1


def test_ai_classify_sets_classification_fields(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(_VALID_CLASSIFICATION))
    p.run([_raw()])
    stored = jobs.find_one()
    assert stored["skills"] == ["Python", "Django"]
    assert stored["seniority"] == "senior"
    assert stored["category"] == "software-engineering"
    assert stored["role_family"] == "backend"


def test_ai_salary_mapped_to_job_salary(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(_VALID_CLASSIFICATION))
    p.run([_raw()])
    stored = jobs.find_one()
    assert stored["salary_min"] == 80_000
    assert stored["salary_max"] == 120_000
    assert stored["currency"] == "EUR"


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


def test_dry_run_skips_persist(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(_VALID_CLASSIFICATION), dry_run=True)
    result = p.run([_raw()])
    assert result.counters.ai_classified == 1
    assert result.counters.dry_run_skipped == 1
    assert result.counters.persisted == 0
    assert jobs.count_documents({}) == 0


def test_dry_run_skips_prefilter_persist(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(), dry_run=True)
    result = p.run([_raw(description="short")])
    assert result.counters.prefilter_rejected == 1
    assert jobs.count_documents({}) == 0


# ---------------------------------------------------------------------------
# Company upsert
# ---------------------------------------------------------------------------


def test_company_upserted(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(_VALID_CLASSIFICATION))
    p.run([_raw(company_name="Acme Corp")])
    doc = companies.find_one({"name_normalized": "acme corp"})
    assert doc is not None
    assert doc["name"] == "Acme Corp"
    assert "trustScore" in doc


def test_company_not_upserted_on_dry_run(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier(_VALID_CLASSIFICATION), dry_run=True)
    p.run([_raw(company_name="Acme Corp")])
    assert companies.count_documents({}) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input(jobs, companies):
    p = pipeline(jobs, companies, classifier=_make_classifier())
    result = p.run([])
    assert result.counters.total == 0
    assert jobs.count_documents({}) == 0


def test_multiple_jobs_with_mixed_outcomes(jobs, companies):
    good = _raw(url="https://a.com/job", title="Senior Engineer")
    bad = _raw(
        url="https://b.com/job",
        title="Junior",
        description="short desc",
    )

    class _MixedClassifier:
        def classify(self, inp):
            if "Senior" in inp["title"]:
                return _VALID_CLASSIFICATION
            return None

    p = pipeline(jobs, companies, classifier=_MixedClassifier())
    result = p.run([good, bad])
    assert result.counters.total == 2
    assert result.counters.prefilter_rejected == 1
    assert result.counters.ai_classified == 1
    assert result.counters.persisted == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url_result(is_valid: bool, reason: str = "", url: str = "https://example.com/job"):
    from pipeline.url_validator import URLValidationResult

    return URLValidationResult(
        url=url,
        is_valid=is_valid,
        status_code=200 if is_valid else 404,
        reason=reason,
    )


def _insert_job(col, **fields) -> ObjectId:
    from models.job import compute_cross_source_hash

    title = fields.get("title", "Software Engineer")
    company_name = fields.get("company_name", "Acme Corp")
    source = fields.get("source", "source_a")
    data = dict(
        url=fields.get("url", "https://example.com/job"),
        dedup_hash=compute_dedup_hash(title, company_name, source),
        cross_source_hash=compute_cross_source_hash(title, company_name),
        source=source,
        title=title,
        title_normalized=title.lower(),
        description=fields.get("description", "a description here"),
        company={
            "name": company_name,
            "name_normalized": company_name.lower(),
        },
        posted_at=_NOW,
        last_seen_at=_NOW,
        updated_at=_NOW,
    )
    return col.insert_one(data).inserted_id
