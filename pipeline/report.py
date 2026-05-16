"""Import-report parent (SDD §A.3 + §I.1).

One document per CLI `import` invocation. Aggregates per-source `ImportRunRecord`s
(via the `report_id` FK).

Collection: `import_reports`. Schema is LOCKED (SDD §I.1) — extra fields belong
on `import_runs` (SDD §I.2), not here.

The tracker is intentionally synchronous: each `add_source` / `add_failure` /
`finish` issues a Mongo $set/$inc/$push. Cost is negligible compared with the
Groq calls dominating an import run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import pymongo
import structlog
from pymongo.collection import Collection

from pipeline.import_run import ImportRunRecord

log = structlog.get_logger(__name__)

_COLLECTION = "import_reports"
_MAX_ERRORS = 100  # SDD §I.1 — truncate stored errors

TriggeredBy = Literal["cli", "cron", "manual"]


@dataclass
class ImportReportRecord:
    """Parent run document. Field set is the SDD §I.1 LOCKED contract."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    ai_model: str = ""
    triggered_by: TriggeredBy = "cli"
    language_targets: list[str] = field(default_factory=list)
    total_sources: int = 0
    total_fetched: int = 0
    total_passed_quality: int = 0
    total_failed_quality: int = 0
    total_upserted: int = 0
    total_skipped_duplicate: int = 0
    total_url_invalid: int = 0
    total_expired_detected: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    avg_enrichment_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_doc(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ai_model": self.ai_model,
            "triggered_by": self.triggered_by,
            "language_targets": list(self.language_targets),
            "total_sources": int(self.total_sources),
            "total_fetched": int(self.total_fetched),
            "total_passed_quality": int(self.total_passed_quality),
            "total_failed_quality": int(self.total_failed_quality),
            "total_upserted": int(self.total_upserted),
            "total_skipped_duplicate": int(self.total_skipped_duplicate),
            "total_url_invalid": int(self.total_url_invalid),
            "total_expired_detected": int(self.total_expired_detected),
            "failure_reasons": dict(self.failure_reasons),
            "avg_enrichment_ms": float(self.avg_enrichment_ms),
            "errors": list(self.errors[:_MAX_ERRORS]),
        }


class ImportReportTracker:
    """Mongo-backed tracker for the `import_reports` collection."""

    def __init__(self, db: pymongo.MongoClient) -> None:  # type: ignore[type-arg]
        self._col: Collection = db[_COLLECTION]  # type: ignore[type-arg]
        # Running enrichment-latency accumulator per run_id (avg pre-finish).
        self._enrichment_ms_sum: dict[str, float] = {}
        self._enrichment_ms_count: dict[str, int] = {}
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        # SDD §I.1 — three indexes required by the backend admin endpoints.
        self._col.create_index([("started_at", pymongo.DESCENDING)])
        self._col.create_index([("run_id", pymongo.ASCENDING)], unique=True)
        self._col.create_index(
            [("ai_model", pymongo.ASCENDING), ("started_at", pymongo.DESCENDING)]
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        ai_model: str,
        language_targets: list[str],
        triggered_by: TriggeredBy = "cli",
    ) -> str:
        """Insert a fresh report document and return its run_id (uuid4 hex)."""
        run_id = uuid.uuid4().hex
        record = ImportReportRecord(
            run_id=run_id,
            started_at=datetime.now(tz=timezone.utc),
            ai_model=ai_model,
            triggered_by=triggered_by,
            language_targets=list(language_targets),
        )
        try:
            self._col.insert_one(record.to_doc())
        except Exception as exc:
            log.error("import_report.start_failed", error=str(exc))
        self._enrichment_ms_sum[run_id] = 0.0
        self._enrichment_ms_count[run_id] = 0
        return run_id

    def add_source(self, run_id: str, source_run: ImportRunRecord) -> None:
        """Fold a per-source `ImportRunRecord` into the parent aggregates."""
        if not run_id:
            return
        inc: dict[str, int] = {
            "total_sources": 1,
            "total_fetched": int(source_run.jobs_fetched),
            "total_passed_quality": int(source_run.passed_quality_gate),
            "total_upserted": int(source_run.jobs_stored),
            "total_skipped_duplicate": int(source_run.jobs_duplicated),
            "total_url_invalid": int(source_run.url_invalid_count),
            "total_expired_detected": int(source_run.soft_404_count),
        }
        # Quality-failures: sum from source_run.failure_reasons. We avoid double
        # counting URL_INVALID / SOFT_404 here because they have dedicated
        # totals above; the controlled-vocab keys still flow through
        # `failure_reasons` for breakdown reporting.
        failed_count = sum(
            v
            for k, v in source_run.failure_reasons.items()
            if k not in ("URL_INVALID", "SOFT_404", "DUPLICATE")
        )
        if failed_count:
            inc["total_failed_quality"] = int(failed_count)

        update_inc: dict[str, int] = {
            f"failure_reasons.{k}": v for k, v in source_run.failure_reasons.items()
        }
        update_inc.update(inc)

        errors_to_push: list[str] = []
        if source_run.errors:
            errors_to_push.extend(source_run.errors[:_MAX_ERRORS])
        if source_run.connector_crashed and source_run.crash_reason:
            errors_to_push.append(
                f"[{source_run.provider_name}] {source_run.crash_reason}"
            )

        try:
            update: dict[str, dict] = {"$inc": update_inc}
            if errors_to_push:
                # $push with $each + $slice keeps `errors` bounded to 100.
                update["$push"] = {
                    "errors": {
                        "$each": errors_to_push,
                        "$slice": -_MAX_ERRORS,
                    }
                }
            self._col.update_one({"run_id": run_id}, update)
        except Exception as exc:
            log.error("import_report.add_source_failed", run_id=run_id, error=str(exc))

    def add_failure(self, run_id: str, reason: str, count: int = 1) -> None:
        """Increment a failure-reason bucket (controlled vocab — SDD §I.3)."""
        if not run_id or not reason:
            return
        try:
            update_inc: dict[str, int] = {f"failure_reasons.{reason}": int(count)}
            # URL_INVALID and SOFT_404 also bump their dedicated counters so
            # the parent doc matches the per-source aggregates.
            if reason == "URL_INVALID":
                update_inc["total_url_invalid"] = int(count)
            elif reason == "SOFT_404":
                update_inc["total_expired_detected"] = int(count)
            elif reason == "DUPLICATE":
                update_inc["total_skipped_duplicate"] = int(count)
            else:
                update_inc["total_failed_quality"] = int(count)
            self._col.update_one(
                {"run_id": run_id},
                {"$inc": update_inc},
            )
        except Exception as exc:
            log.error(
                "import_report.add_failure_failed",
                run_id=run_id,
                reason=reason,
                error=str(exc),
            )

    def record_enrichment_ms(self, run_id: str, elapsed_ms: float) -> None:
        """Accumulate per-job Groq latency; persisted on `finish()`."""
        if not run_id:
            return
        self._enrichment_ms_sum[run_id] = self._enrichment_ms_sum.get(run_id, 0.0) + float(
            elapsed_ms
        )
        self._enrichment_ms_count[run_id] = self._enrichment_ms_count.get(run_id, 0) + 1

    def finish(self, run_id: str) -> None:
        """Stamp finished_at + final avg_enrichment_ms."""
        if not run_id:
            return
        total = self._enrichment_ms_sum.get(run_id, 0.0)
        n = self._enrichment_ms_count.get(run_id, 0)
        avg = (total / n) if n else 0.0
        try:
            self._col.update_one(
                {"run_id": run_id},
                {
                    "$set": {
                        "finished_at": datetime.now(tz=timezone.utc),
                        "avg_enrichment_ms": float(avg),
                    }
                },
            )
        except Exception as exc:
            log.error("import_report.finish_failed", run_id=run_id, error=str(exc))
        # Cleanup accumulators
        self._enrichment_ms_sum.pop(run_id, None)
        self._enrichment_ms_count.pop(run_id, None)


__all__ = [
    "ImportReportRecord",
    "ImportReportTracker",
]
