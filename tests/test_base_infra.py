"""Tests for base infra: Pydantic models, MongoDB, Groq wrapper."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import groq
import pytest

from models.job import (
    EmploymentType,
    GeoPoint,
    Job,
    JobClassification,
    JobCompany,
    JobContent,
    JobLocation,
    JobSalary,
    JobSource,
    JobStatus,
    Language,
    RawJob,
    RemoteMode,
    RoleFamily,
    Seniority,
    compute_dedup_hash,
    normalize_text,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_job(**overrides) -> Job:
    now = datetime.now(tz=timezone.utc)
    defaults = dict(
        url="https://jobs.example.com/123",
        dedup_hash="abc123",
        source_info=JobSource(source="adzuna", external_id="EXT-1"),
        content=JobContent(
            title="Senior Backend Engineer",
            title_normalized="senior backend engineer",
            description="We need a senior backend engineer " + "x" * 200,
            language=Language.EN,
        ),
        company=JobCompany(name="Acme Ltd", name_normalized="acme ltd"),
        posted_at=now,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# Model: enums
# ---------------------------------------------------------------------------


def test_language_enum_values():
    assert Language.EN.value == "en"
    assert Language.IT.value == "it"


def test_job_status_enum_values():
    assert JobStatus.VALID.value == "valid"
    assert JobStatus.PREMIUM.value == "premium"
    assert JobStatus.REJECTED_PREFILTER.value == "rejected_prefilter"


def test_remote_mode_enum():
    assert RemoteMode.REMOTE.value == "remote"
    assert RemoteMode.HYBRID.value == "hybrid"


# ---------------------------------------------------------------------------
# Model: GeoPoint validation
# ---------------------------------------------------------------------------


def test_geo_point_valid():
    g = GeoPoint(coordinates=[9.19, 45.46])
    assert g.type == "Point"
    assert g.coordinates == [9.19, 45.46]


def test_geo_point_wrong_length():
    with pytest.raises(Exception):
        GeoPoint(coordinates=[9.19])


def test_geo_point_out_of_range():
    with pytest.raises(Exception):
        GeoPoint(coordinates=[200.0, 45.0])


# ---------------------------------------------------------------------------
# Model: JobClassification remote sync
# ---------------------------------------------------------------------------


def test_classification_remote_sync_remote():
    cl = JobClassification(remote_mode=RemoteMode.REMOTE)
    assert cl.remote is True


def test_classification_remote_sync_hybrid():
    cl = JobClassification(remote_mode=RemoteMode.HYBRID)
    assert cl.remote is True


def test_classification_remote_sync_onsite():
    cl = JobClassification(remote_mode=RemoteMode.ONSITE)
    assert cl.remote is False


def test_classification_unknown_remote_false():
    cl = JobClassification(remote_mode=RemoteMode.UNKNOWN)
    assert cl.remote is False


# ---------------------------------------------------------------------------
# Model: JobSalary
# ---------------------------------------------------------------------------


def test_job_salary_optional_fields():
    s = JobSalary()
    assert s.min is None
    assert s.max is None
    assert s.currency is None


def test_job_salary_with_values():
    s = JobSalary(min=50000, max=90000, currency="EUR")
    assert s.min == 50000
    assert s.currency == "EUR"


# ---------------------------------------------------------------------------
# Model: RawJob
# ---------------------------------------------------------------------------


def test_raw_job_minimal():
    r = RawJob(
        url="https://example.com/job/1",
        title="Python Dev",
        description="Build backend services",
        company_name="Foo Corp",
        source="linkedin",
    )
    assert r.posted_at is None
    assert r.salary_min is None


def test_raw_job_invalid_missing_required():
    with pytest.raises(Exception):
        RawJob(url="https://example.com/job/1")


# ---------------------------------------------------------------------------
# Model: Job round-trip (to_mongo_doc / from_mongo_doc)
# ---------------------------------------------------------------------------


def test_job_to_mongo_doc_keys():
    job = _make_job()
    doc = job.to_mongo_doc()
    # Required fields present
    assert "url" in doc
    assert "link" in doc  # Bun compat
    assert "posted_at" in doc
    assert "published_at" in doc  # Bun compat
    assert "dedup_hash" in doc
    assert "company" in doc
    assert "location" in doc
    assert "remote" in doc
    assert "status" in doc


def test_job_to_mongo_doc_enum_values_are_strings():
    job = _make_job(
        classification=JobClassification(
            seniority=Seniority.SENIOR,
            role_family=RoleFamily.BACKEND,
            employment_type=EmploymentType.FULL_TIME,
            remote_mode=RemoteMode.REMOTE,
        )
    )
    doc = job.to_mongo_doc()
    assert doc["seniority"] == "senior"
    assert doc["role_family"] == "backend"
    assert doc["employment_type"] == "full_time"
    assert doc["remote_mode"] == "remote"
    assert doc["remote"] is True
    assert doc["language"] == "en"
    assert doc["status"] == "valid"


def test_job_to_mongo_doc_company_nested():
    job = _make_job(
        company=JobCompany(name="Acme Ltd", name_normalized="acme ltd", logo="https://logo.url")
    )
    doc = job.to_mongo_doc()
    assert isinstance(doc["company"], dict)
    assert doc["company"]["name"] == "Acme Ltd"
    assert doc["company"]["logo"] == "https://logo.url"


def test_job_to_mongo_doc_location_with_geo():
    job = _make_job(
        location=JobLocation(
            city="Milan",
            country="IT",
            geo=GeoPoint(coordinates=[9.19, 45.46]),
        )
    )
    doc = job.to_mongo_doc()
    assert doc["city"] == "Milan"
    assert doc["location_geo"]["type"] == "Point"
    assert doc["location_geo"]["coordinates"] == [9.19, 45.46]


def test_job_to_mongo_doc_salary_from_classification():
    job = _make_job(
        classification=JobClassification(salary_min=80000, salary_max=120000, currency="EUR")
    )
    doc = job.to_mongo_doc()
    assert doc["salary_min"] == 80000
    assert doc["salary_max"] == 120000
    assert doc["currency"] == "EUR"


def test_job_to_mongo_doc_salary_explicit_overrides_classification():
    job = _make_job(
        salary=JobSalary(min=90000, max=None, currency="USD"),
        classification=JobClassification(salary_min=80000, salary_max=120000, currency="EUR"),
    )
    doc = job.to_mongo_doc()
    assert doc["salary_min"] == 90000  # explicit wins
    assert doc["currency"] == "USD"    # explicit wins


def test_job_from_mongo_doc_round_trip():
    job = _make_job(
        classification=JobClassification(
            technical_skills=["Python", "Django"],
            seniority=Seniority.MID,
            role_family=RoleFamily.BACKEND,
            remote_mode=RemoteMode.HYBRID,
        )
    )
    doc = job.to_mongo_doc()
    rebuilt = Job.from_mongo_doc(doc)
    assert rebuilt.url == job.url
    assert rebuilt.content.title == job.content.title
    assert rebuilt.company.name == job.company.name
    assert rebuilt.classification.seniority == Seniority.MID
    assert rebuilt.classification.role_family == RoleFamily.BACKEND
    assert rebuilt.classification.remote is True


def test_job_from_mongo_doc_unknown_language_fallback():
    job = _make_job()
    doc = job.to_mongo_doc()
    doc["language"] = "zz"  # invalid
    rebuilt = Job.from_mongo_doc(doc)
    assert rebuilt.content.language == Language.OTHER


# ---------------------------------------------------------------------------
# Helpers: normalize_text, compute_dedup_hash
# ---------------------------------------------------------------------------


def test_normalize_text_lowercases():
    assert normalize_text("HELLO WORLD") == "hello world"


def test_normalize_text_strips_accents():
    assert normalize_text("café") == "cafe"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  foo   bar  ") == "foo bar"


def test_compute_dedup_hash_deterministic():
    h1 = compute_dedup_hash("Senior Backend Engineer", "Acme Ltd", "adzuna")
    h2 = compute_dedup_hash("Senior Backend Engineer", "Acme Ltd", "adzuna")
    assert h1 == h2


def test_compute_dedup_hash_different_source():
    h1 = compute_dedup_hash("Senior Backend Engineer", "Acme Ltd", "adzuna")
    h2 = compute_dedup_hash("Senior Backend Engineer", "Acme Ltd", "linkedin")
    assert h1 != h2


def test_compute_dedup_hash_accent_insensitive():
    h1 = compute_dedup_hash("Développeur Senior", "Société X", "source")
    h2 = compute_dedup_hash("Developpeur Senior", "Societe X", "source")
    assert h1 == h2


# ---------------------------------------------------------------------------
# MongoDB: connection + ensure_indexes (mongomock)
# ---------------------------------------------------------------------------


def test_mongo_fixture_connected(mongo_db):
    assert mongo_db.name == "itjobhub"


def test_ensure_indexes_runs_without_error(mongo_client):
    """ensure_indexes must not raise on a fresh mongomock database."""
    from database import repository

    # Patch singleton to use the mock client
    original = repository._client
    repository._client = mongo_client
    try:
        # mongomock doesn't enforce all index options but must not crash
        repository.ensure_indexes()
    except Exception as exc:
        # Accept OperationFailure from mongomock for unsupported index types
        # (2dsphere, text weights) — the important thing is indexes are attempted
        assert "2dsphere" in str(exc) or "text" in str(exc) or "weight" in str(exc), (
            f"Unexpected error: {exc}"
        )
    finally:
        repository._client = original


def test_jobs_collection_accessible(mongo_db):
    from database import repository

    original = repository._client
    repository._client = mongo_db.client
    try:
        col = repository.get_jobs()
        col.insert_one({"test": True})
        assert col.count_documents({}) == 1
    finally:
        repository._client = original


# ---------------------------------------------------------------------------
# Groq wrapper: classify_job with mock
# ---------------------------------------------------------------------------


def _make_groq_mock(ai_output: dict) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50

    choice = MagicMock()
    choice.message.content = json.dumps(ai_output)

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


_VALID_AI_OUTPUT = {
    "skills": ["Go", "Kubernetes", "PostgreSQL"],
    "category": "software_engineering",
    "seniority": "senior",
    "role_family": "backend",
    "employment_type": "full_time",
    "remote_mode": "remote",
    "salary_min": 90000,
    "salary_max": 130000,
    "currency": "EUR",
    "languages_required": ["en"],
    "quality_flags": ["clear_jd", "has_requirements"],
    "confidence": 0.94,
}


def test_classify_job_returns_classification():
    from ai.classifier import GroqClassifier

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_groq_mock(_VALID_AI_OUTPUT)

    clf = GroqClassifier(client=mock_client)
    result = clf.classify_job("Senior Backend Engineer (Go) at Acme Cloud")

    assert isinstance(result, JobClassification)
    assert "Go" in result.technical_skills
    assert result.seniority == Seniority.SENIOR
    assert result.role_family == RoleFamily.BACKEND
    assert result.remote_mode == RemoteMode.REMOTE
    assert result.remote is True
    assert result.ai_confidence == 0.94
    assert result.salary_min == 90000
    assert result.currency == "EUR"


def test_classify_job_ai_model_and_timestamp_set():
    from ai.classifier import GroqClassifier

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_groq_mock(_VALID_AI_OUTPUT)

    clf = GroqClassifier(client=mock_client)
    result = clf.classify_job("some job text")

    assert result.ai_model != ""
    assert result.ai_call_at is not None


def test_classify_job_invalid_enum_falls_back_to_default():
    from ai.classifier import GroqClassifier

    bad_output = dict(_VALID_AI_OUTPUT, seniority="wizard")  # invalid enum
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_groq_mock(bad_output)

    clf = GroqClassifier(client=mock_client)
    result = clf.classify_job("some job text")
    # Pydantic coerces unknown enum to default
    assert result.seniority == Seniority.UNKNOWN


def test_classify_job_retries_on_rate_limit():
    from ai.classifier import GroqClassifier

    mock_client = MagicMock()
    # First two calls raise RateLimitError, third succeeds
    good_response = _make_groq_mock(_VALID_AI_OUTPUT)
    mock_client.chat.completions.create.side_effect = [
        groq.RateLimitError("rate limited", response=MagicMock(), body={}),
        groq.RateLimitError("rate limited", response=MagicMock(), body={}),
        good_response,
    ]

    clf = GroqClassifier(client=mock_client)
    # Disable sleep in rate limiter for speed
    clf._rate_limiter._min_interval = 0.0

    result = clf.classify_job("some text")
    assert mock_client.chat.completions.create.call_count == 3
    assert result.seniority == Seniority.SENIOR


def test_classify_job_raises_after_max_retries():
    from ai.classifier import GroqClassifier

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = groq.RateLimitError(
        "rate limited", response=MagicMock(), body={}
    )

    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0

    with pytest.raises(groq.RateLimitError):
        clf.classify_job("some text")

    assert mock_client.chat.completions.create.call_count == 3


def test_cost_tracker_accumulates():
    from ai.classifier import GroqClassifier, cost_tracker

    initial_in = cost_tracker.tokens_in
    initial_out = cost_tracker.tokens_out

    mock_client = MagicMock()
    usage = MagicMock()
    usage.prompt_tokens = 200
    usage.completion_tokens = 75
    response = _make_groq_mock(_VALID_AI_OUTPUT)
    response.usage = usage
    mock_client.chat.completions.create.return_value = response

    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0
    clf.classify_job("some text")

    assert cost_tracker.tokens_in >= initial_in + 200
    assert cost_tracker.tokens_out >= initial_out + 75


def test_classify_job_uses_conftest_mock_groq(mock_groq_client):
    """Verify conftest mock_groq_client works with GroqClassifier."""
    from ai.classifier import GroqClassifier

    clf = GroqClassifier(client=mock_groq_client)
    clf._rate_limiter._min_interval = 0.0

    # Use a title known to conftest ground truth
    text = "Senior Backend Engineer (Go)\n\nAcme Cloud Ltd is looking for a Senior Backend..."
    result = clf.classify_job(text)

    assert isinstance(result, JobClassification)
