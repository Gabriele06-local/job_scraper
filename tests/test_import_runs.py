"""Tests for ImportRunRecord persistence and auto-disable logic."""

from __future__ import annotations

from datetime import datetime, timezone

import mongomock
import pytest

from pipeline.import_run import ImportRunRecord, ImportRunTracker


@pytest.fixture
def tracker() -> ImportRunTracker:
    client = mongomock.MongoClient()
    db = client["testdb"]
    return ImportRunTracker(db)


def _make_record(
    provider: str,
    status: str = "success",
    fetched: int = 100,
    stored: int = 80,
) -> ImportRunRecord:
    now = datetime.now(tz=timezone.utc)
    return ImportRunRecord(
        provider_name=provider,
        started_at=now,
        completed_at=now,
        jobs_fetched=fetched,
        jobs_stored=stored,
        jobs_discarded=fetched - stored,
        jobs_duplicated=0,
        status=status,  # type: ignore[arg-type]
    )


def test_save_and_retrieve(tracker: ImportRunTracker) -> None:
    record = _make_record("adzuna", status="success")
    tracker.save(record)
    doc = tracker._col.find_one({"provider_name": "adzuna"})
    assert doc is not None
    assert doc["jobs_fetched"] == 100
    assert doc["status"] == "success"


def test_no_disable_after_one_failure(tracker: ImportRunTracker) -> None:
    tracker.save(_make_record("reed", status="failed"))
    assert not tracker.should_disable("reed")


def test_no_disable_after_two_failures(tracker: ImportRunTracker) -> None:
    tracker.save(_make_record("reed", status="failed"))
    tracker.save(_make_record("reed", status="failed"))
    assert not tracker.should_disable("reed")


def test_disable_after_three_consecutive_failures(tracker: ImportRunTracker) -> None:
    for _ in range(3):
        tracker.save(_make_record("reed", status="failed"))
    assert tracker.should_disable("reed")


def test_success_resets_disable_flag(tracker: ImportRunTracker) -> None:
    tracker.save(_make_record("reed", status="failed"))
    tracker.save(_make_record("reed", status="failed"))
    tracker.save(_make_record("reed", status="success"))
    assert not tracker.should_disable("reed")


def test_consecutive_failures_count(tracker: ImportRunTracker) -> None:
    tracker.save(_make_record("lever", status="failed"))
    tracker.save(_make_record("lever", status="failed"))
    assert tracker.consecutive_failures("lever") == 2


def test_consecutive_failures_resets_after_success(tracker: ImportRunTracker) -> None:
    tracker.save(_make_record("lever", status="failed"))
    tracker.save(_make_record("lever", status="success"))
    tracker.save(_make_record("lever", status="failed"))
    assert tracker.consecutive_failures("lever") == 1


def test_record_to_doc_fields() -> None:
    now = datetime.now(tz=timezone.utc)
    r = ImportRunRecord(
        provider_name="greenhouse",
        started_at=now,
        completed_at=now,
        jobs_fetched=50,
        jobs_stored=40,
        jobs_discarded=8,
        jobs_duplicated=2,
        status="partial",
        error_message="partial timeout",
    )
    doc = r.to_doc()
    assert doc["provider_name"] == "greenhouse"
    assert doc["status"] == "partial"
    assert doc["error_message"] == "partial timeout"
    assert doc["jobs_duplicated"] == 2
