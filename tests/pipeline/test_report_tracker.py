"""Tests for `pipeline/report.py:ImportReportTracker` (SDD §A.3 / §I.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import mongomock
import pytest

from pipeline.import_run import ImportRunRecord
from pipeline.report import ImportReportTracker


@pytest.fixture
def db() -> mongomock.Database:
    client = mongomock.MongoClient()
    return client["testdb"]


def _make_run(
    provider: str,
    *,
    fetched: int = 100,
    stored: int = 80,
    failures: dict[str, int] | None = None,
    crashed: bool = False,
    crash_reason: str | None = None,
    url_invalid: int = 0,
    soft_404: int = 0,
    duplicated: int = 0,
    passed: int = 0,
) -> ImportRunRecord:
    now = datetime.now(tz=timezone.utc)
    return ImportRunRecord(
        provider_name=provider,
        started_at=now,
        completed_at=now,
        jobs_fetched=fetched,
        jobs_stored=stored,
        jobs_duplicated=duplicated,
        passed_quality_gate=passed,
        url_invalid_count=url_invalid,
        soft_404_count=soft_404,
        failure_reasons=dict(failures or {}),
        connector_crashed=crashed,
        crash_reason=crash_reason,
        status="success",
    )


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_indexes_created_on_init(db: mongomock.Database) -> None:
    ImportReportTracker(db)
    names = {ix["name"] for ix in db["import_reports"].list_indexes()}
    # mongomock generates implicit names — verify by index keys
    keys = {tuple(ix["key"].items()) for ix in db["import_reports"].list_indexes()}
    assert (("started_at", -1),) in keys
    assert (("run_id", 1),) in keys
    assert (("ai_model", 1), ("started_at", -1)) in keys
    assert names  # at least one index materialized


# ---------------------------------------------------------------------------
# Lifecycle: start → add_source x3 → add_failure → finish
# ---------------------------------------------------------------------------


def test_start_creates_document_with_locked_fields(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="llama-3.1-8b-instant", language_targets=["it", "en"])

    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc is not None
    # SDD §I.1 LOCKED fields:
    for field in [
        "run_id",
        "started_at",
        "finished_at",
        "ai_model",
        "triggered_by",
        "language_targets",
        "total_sources",
        "total_fetched",
        "total_passed_quality",
        "total_failed_quality",
        "total_upserted",
        "total_skipped_duplicate",
        "total_url_invalid",
        "total_expired_detected",
        "failure_reasons",
        "avg_enrichment_ms",
        "errors",
    ]:
        assert field in doc, f"missing locked field {field}"
    assert doc["finished_at"] is None
    assert doc["ai_model"] == "llama-3.1-8b-instant"
    assert doc["triggered_by"] == "cli"
    assert doc["language_targets"] == ["it", "en"]


def test_aggregate_sources_and_failures(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="llama-3.1-8b-instant", language_targets=["it"])

    tracker.add_source(
        run_id,
        _make_run(
            "adzuna",
            fetched=100,
            stored=80,
            passed=70,
            url_invalid=2,
            soft_404=1,
            duplicated=5,
            failures={"MISSING_COMPANY": 3, "ZERO_SKILLS": 2},
        ),
    )
    tracker.add_source(
        run_id,
        _make_run(
            "greenhouse",
            fetched=50,
            stored=45,
            passed=40,
            url_invalid=1,
            failures={"MISSING_COMPANY": 1, "DESCRIPTION_INVALID": 4},
        ),
    )
    tracker.add_source(
        run_id,
        _make_run(
            "lever",
            fetched=20,
            stored=18,
            passed=15,
            crashed=True,
            crash_reason="ConnectionError",
        ),
    )

    tracker.add_failure(run_id, "AI_CLASSIFICATION_FAILED", count=2)
    tracker.add_failure(run_id, "URL_INVALID", count=3)

    tracker.finish(run_id)

    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc is not None
    assert doc["total_sources"] == 3
    assert doc["total_fetched"] == 100 + 50 + 20
    assert doc["total_passed_quality"] == 70 + 40 + 15
    assert doc["total_upserted"] == 80 + 45 + 18
    assert doc["total_skipped_duplicate"] == 5  # only adzuna duplicated
    # url_invalid: 2 + 1 + 3 (explicit add_failure)
    assert doc["total_url_invalid"] == 6
    assert doc["total_expired_detected"] == 1  # soft_404
    # failure_reasons rolled up correctly
    assert doc["failure_reasons"]["MISSING_COMPANY"] == 4
    assert doc["failure_reasons"]["ZERO_SKILLS"] == 2
    assert doc["failure_reasons"]["DESCRIPTION_INVALID"] == 4
    assert doc["failure_reasons"]["AI_CLASSIFICATION_FAILED"] == 2
    assert doc["failure_reasons"]["URL_INVALID"] == 3
    # crash captured in errors
    assert any("lever" in e and "ConnectionError" in e for e in doc["errors"])
    # finish stamps finished_at
    assert doc["finished_at"] is not None


def test_finish_persists_avg_enrichment_ms(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="llama-3.1-8b-instant", language_targets=["en"])
    tracker.record_enrichment_ms(run_id, 100.0)
    tracker.record_enrichment_ms(run_id, 200.0)
    tracker.record_enrichment_ms(run_id, 300.0)
    tracker.finish(run_id)

    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc["avg_enrichment_ms"] == pytest.approx(200.0, rel=1e-6)


def test_add_failure_with_unknown_run_id_is_noop(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    tracker.add_failure("", "ZERO_SKILLS")  # silent no-op
    assert db["import_reports"].count_documents({}) == 0


# ---------------------------------------------------------------------------
# Failure reason counters
# ---------------------------------------------------------------------------


def test_add_failure_url_invalid_bumps_dedicated_counter(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    tracker.add_failure(run_id, "URL_INVALID", count=2)
    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc["total_url_invalid"] == 2


def test_add_failure_soft_404_bumps_dedicated_counter(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    tracker.add_failure(run_id, "SOFT_404", count=1)
    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc["total_expired_detected"] == 1


def test_add_failure_duplicate_bumps_dedicated_counter(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    tracker.add_failure(run_id, "DUPLICATE", count=3)
    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc["total_skipped_duplicate"] == 3


# ---------------------------------------------------------------------------
# Early-return guards (empty run_id)
# ---------------------------------------------------------------------------


def test_add_source_empty_run_id_is_silent(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    tracker.add_source("", _make_run("test"))
    assert db["import_reports"].count_documents({}) == 0


def test_record_enrichment_ms_empty_run_id_is_silent(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    tracker.record_enrichment_ms("", 100.0)  # no crash


def test_finish_empty_run_id_is_silent(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    tracker.finish("")  # no crash


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def test_start_insert_exception_logged(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    # Mock insert_one to raise
    with patch.object(tracker._col, "insert_one", side_effect=Exception("db error")):
        run_id = tracker.start(ai_model="x", language_targets=["en"])
        assert run_id is not None


def test_add_source_update_exception_logged(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    with patch.object(tracker._col, "update_one", side_effect=Exception("db error")):
        tracker.add_source(run_id, _make_run("test"))  # no crash


def test_add_failure_update_exception_logged(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    with patch.object(tracker._col, "update_one", side_effect=Exception("db error")):
        tracker.add_failure(run_id, "ZERO_SKILLS")  # no crash


def test_finish_update_exception_logged(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    with patch.object(tracker._col, "update_one", side_effect=Exception("db error")):
        tracker.finish(run_id)  # no crash


# ---------------------------------------------------------------------------
# Add source with errors
# ---------------------------------------------------------------------------


def test_add_source_with_errors_pushes_errors(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    run_id = tracker.start(ai_model="x", language_targets=["en"])
    run = _make_run("test", failures={"ZERO_SKILLS": 1})
    run.errors = ["something went wrong"]
    tracker.add_source(run_id, run)
    doc = db["import_reports"].find_one({"run_id": run_id})
    assert doc is not None
    assert len(doc.get("errors", [])) >= 1


# ---------------------------------------------------------------------------
# Index conflict (OperationFailure with code != 85 re-raises)
# ---------------------------------------------------------------------------


def test_index_operation_failure_non_conflict_raises(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    op_failure = pytest.importorskip("pymongo.errors").OperationFailure
    with patch.object(
        tracker._col, "create_index", side_effect=op_failure("other error", code=86)
    ):
        with pytest.raises(op_failure):
            tracker._ensure_indexes()


def test_index_operation_failure_code_85_logged_not_raised(db: mongomock.Database) -> None:
    tracker = ImportReportTracker(db)
    op_failure = pytest.importorskip("pymongo.errors").OperationFailure
    with patch.object(tracker._col, "create_index") as mock_create:
        mock_create.side_effect = op_failure("index already exists under different name", code=85)
        tracker._ensure_indexes()  # must not raise
