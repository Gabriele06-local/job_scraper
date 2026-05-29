"""Tests for pipeline/dedupe.py — L2/L2b/L3 dedup logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import mongomock
import pytest

from models.job import Job, RawJob
from pipeline.dedupe import (
    check_fuzzy_dup,
    find_cross_source_dup,
    find_existing_job,
    merge_cross_source,
    merge_with_existing,
)


@pytest.fixture
def jobs():
    client = mongomock.MongoClient()
    col = client["testdb"]["jobs"]
    col.create_index("dedup_hash", sparse=True)
    col.create_index("cross_source_hash", sparse=True)
    col.create_index("source")
    return col


_NOW = datetime.now(tz=timezone.utc)
_YESTERDAY = _NOW - timedelta(days=1)


def _make_job_doc(
    *,
    url: str = "https://x.io/a",
    dedup_hash: str = "hash_a",
    cross_source_hash: str = "csh_a",
    source: str = "source_a",
    title: str = "Software Engineer",
    title_normalized: str = "software engineer",
    company: str = "Acme",
    company_normalized: str = "acme",
    posted_at= _YESTERDAY,
    description: str = "Some description",
    salary_min: int | None = None,
    salary_max: int | None = None,
    last_seen_at = _YESTERDAY,
    updated_at = _YESTERDAY,
    created_at = _YESTERDAY,
    first_seen_at = _YESTERDAY,
) -> dict:
    return {
        "url": url,
        "dedup_hash": dedup_hash,
        "cross_source_hash": cross_source_hash,
        "source": source,
        "title": title,
        "title_normalized": title_normalized,
        "description": description,
        "company": {"name": company, "name_normalized": company_normalized},
        "posted_at": posted_at,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "last_seen_at": last_seen_at,
        "updated_at": updated_at,
        "created_at": created_at,
        "first_seen_at": first_seen_at,
    }


def _make_raw(source: str = "source_b", title: str = "Software Engineer", **overrides) -> RawJob:
    data = dict(
        url="https://y.io/a",
        title=title,
        description="Some description",
        company_name="Acme",
        source=source,
        posted_at=_NOW,
    )
    data.update(overrides)
    return RawJob(**data)


# ---------------------------------------------------------------------------
# find_existing_job
# ---------------------------------------------------------------------------


def test_find_existing_job_found(jobs):
    doc = _make_job_doc(dedup_hash="abc123")
    jobs.insert_one(doc)
    result = find_existing_job("abc123", jobs)
    assert result is not None
    assert result.dedup_hash == "abc123"


def test_find_existing_job_not_found(jobs):
    assert find_existing_job("nonexistent", jobs) is None


# ---------------------------------------------------------------------------
# merge_with_existing
# ---------------------------------------------------------------------------


def test_merge_with_existing_increments_seen_count(jobs):
    doc = _make_job_doc()
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw()

    merge_with_existing(existing, raw, jobs)

    stored = jobs.find_one({"dedup_hash": "hash_a"})
    assert stored["seen_count"] == 1


def test_merge_with_existing_keeps_earliest_posted_at(jobs):
    older = _NOW - timedelta(days=5)
    doc = _make_job_doc(posted_at=_NOW)
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(posted_at=older)

    updated = merge_with_existing(existing, raw, jobs)

    assert updated.posted_at.replace(microsecond=0) == older.replace(microsecond=0)
    stored = jobs.find_one({"dedup_hash": "hash_a"})
    stored_dt = stored["posted_at"].replace(tzinfo=timezone.utc).replace(microsecond=0)
    assert stored_dt == older.replace(microsecond=0)


def test_merge_with_existing_keeps_existing_posted_at_when_newer(jobs):
    doc = _make_job_doc(posted_at=_YESTERDAY)
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(posted_at=_NOW)

    updated = merge_with_existing(existing, raw, jobs)

    assert updated.posted_at == _YESTERDAY


def test_merge_with_existing_refreshes_last_seen(jobs):
    doc = _make_job_doc(last_seen_at=_YESTERDAY)
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw()

    updated = merge_with_existing(existing, raw, jobs)

    assert updated.last_seen_at > _YESTERDAY


# ---------------------------------------------------------------------------
# find_cross_source_dup
# ---------------------------------------------------------------------------


def test_find_cross_source_dup_found(jobs):
    doc = _make_job_doc(cross_source_hash="csh_x", source="source_a")
    jobs.insert_one(doc)
    result = find_cross_source_dup("csh_x", "source_b", jobs)
    assert result is not None
    assert result.cross_source_hash == "csh_x"


def test_find_cross_source_dup_same_source_excluded(jobs):
    doc = _make_job_doc(cross_source_hash="csh_x", source="source_a")
    jobs.insert_one(doc)
    result = find_cross_source_dup("csh_x", "source_a", jobs)
    assert result is None


def test_find_cross_source_dup_not_found(jobs):
    assert find_cross_source_dup("csh_nope", "source_b", jobs) is None


# ---------------------------------------------------------------------------
# merge_cross_source
# ---------------------------------------------------------------------------


def test_merge_cross_source_keeps_longer_description(jobs):
    doc = _make_job_doc(description="short")
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(description="longer description here")

    updated = merge_cross_source(existing, raw, jobs)

    assert updated.content.description == "longer description here"


def test_merge_cross_source_keeps_existing_description_when_longer(jobs):
    doc = _make_job_doc(description="longer existing description")
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(description="short")

    updated = merge_cross_source(existing, raw, jobs)

    assert updated.content.description == "longer existing description"


def test_merge_cross_source_widens_salary_range(jobs):
    doc = _make_job_doc(salary_min=50, salary_max=100)
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(salary_min=30, salary_max=120)

    updated = merge_cross_source(existing, raw, jobs)

    assert updated.salary.min == 30
    assert updated.salary.max == 120


def test_merge_cross_source_keeps_existing_salary_when_narrower(jobs):
    doc = _make_job_doc(salary_min=50, salary_max=100)
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(salary_min=60, salary_max=90)

    updated = merge_cross_source(existing, raw, jobs)

    assert updated.salary.min == 50
    assert updated.salary.max == 100


def test_merge_cross_source_keeps_earliest_posted_at(jobs):
    older = _NOW - timedelta(days=5)
    doc = _make_job_doc(posted_at=_NOW)
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw(posted_at=older)

    updated = merge_cross_source(existing, raw, jobs)

    assert updated.posted_at == older


def test_merge_cross_source_increments_seen_count(jobs):
    doc = _make_job_doc()
    jobs.insert_one(doc)
    existing = Job.from_mongo_doc(doc | {"_id": doc.get("_id")})
    raw = _make_raw()

    merge_cross_source(existing, raw, jobs)

    stored = jobs.find_one({"dedup_hash": "hash_a"})
    assert stored["seen_count"] == 1


# ---------------------------------------------------------------------------
# check_fuzzy_dup
# ---------------------------------------------------------------------------


def test_check_fuzzy_dup_hit(jobs):
    doc = _make_job_doc(
        source="source_a",
        title="Senior Software Engineer",
        title_normalized="senior software engineer",
        company="Acme",
        company_normalized="acme",
        posted_at=_YESTERDAY,
    )
    jobs.insert_one(doc)
    raw = _make_raw(source="source_b", title="Senior Software Engineer")

    result = check_fuzzy_dup(raw, jobs)

    assert result is not None
    assert result.url == "https://x.io/a"


def test_check_fuzzy_dup_different_company_no_match(jobs):
    doc = _make_job_doc(
        source="source_a",
        title="Software Engineer",
        title_normalized="software engineer",
        company="Acme",
        company_normalized="acme",
        posted_at=_YESTERDAY,
    )
    jobs.insert_one(doc)
    raw = _make_raw(source="source_b", title="Software Engineer", company_name="OtherCorp")

    result = check_fuzzy_dup(raw, jobs)

    assert result is None


def test_check_fuzzy_dup_same_source_excluded(jobs):
    doc = _make_job_doc(
        source="source_a",
        title="Software Engineer",
        title_normalized="software engineer",
        company="Acme",
        company_normalized="acme",
        posted_at=_YESTERDAY,
    )
    jobs.insert_one(doc)
    raw = _make_raw(source="source_a", title="Software Engineer")

    result = check_fuzzy_dup(raw, jobs)

    assert result is None


def test_check_fuzzy_dup_outside_window_no_match(jobs):
    old = _NOW - timedelta(days=30)
    doc = _make_job_doc(
        source="source_a",
        title="Software Engineer",
        title_normalized="software engineer",
        company="Acme",
        company_normalized="acme",
        posted_at=old,
    )
    jobs.insert_one(doc)
    raw = _make_raw(source="source_b", title="Software Engineer")

    result = check_fuzzy_dup(raw, jobs)

    assert result is None


def test_check_fuzzy_dup_low_score_no_match(jobs):
    doc = _make_job_doc(
        source="source_a",
        title="Software Engineer",
        title_normalized="software engineer",
        company="Acme",
        company_normalized="acme",
        posted_at=_YESTERDAY,
    )
    jobs.insert_one(doc)
    raw = _make_raw(source="source_b", title="Completely Different Title")

    result = check_fuzzy_dup(raw, jobs)

    assert result is None


def test_check_fuzzy_dup_no_candidates_returns_none(jobs):
    raw = _make_raw(source="source_b", title="Software Engineer")
    result = check_fuzzy_dup(raw, jobs)
    assert result is None
