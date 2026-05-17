"""Import run tracking — per-provider child of `import_reports`.

Collection: `import_runs`
Schema: see SDD §I.2 (LOCKED CONTRACT — backend Prisma model depends on it).
Auto-disable logic: 3 consecutive failures → caller flips the REGISTRY entry.

Migration note: the new fields (report_id, language_target, passed_quality_gate,
failure_reasons, avg_enrichment_ms, errors, quality_score, connector_crashed,
crash_reason, soft_404_count, url_invalid_count) all have defaults; readers
must treat absent keys as None / 0 / {} for backward-compat with pre-existing
documents (no backfill).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pymongo
from pymongo.errors import OperationFailure
import structlog
from pymongo.collection import Collection

log = structlog.get_logger(__name__)

_COLLECTION = "import_runs"
_MAX_CONSECUTIVE_FAILURES = 3
_MAX_ERRORS = 100


@dataclass
class ImportRunRecord:
    """Per-source run record. SDD §I.2."""

    provider_name: str
    started_at: datetime
    completed_at: datetime | None = None
    jobs_fetched: int = 0
    jobs_stored: int = 0
    jobs_discarded: int = 0
    jobs_duplicated: int = 0
    status: Literal["success", "partial", "failed"] = "failed"
    error_message: str | None = None
    # ----- SDD §I.2 additions ------------------------------------------------
    report_id: str | None = None
    language_target: str | None = None
    passed_quality_gate: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    avg_enrichment_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    connector_crashed: bool = False
    crash_reason: str | None = None
    soft_404_count: int = 0
    url_invalid_count: int = 0

    def to_doc(self) -> dict:
        return {
            "provider_name": self.provider_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "jobs_fetched": self.jobs_fetched,
            "jobs_stored": self.jobs_stored,
            "jobs_discarded": self.jobs_discarded,
            "jobs_duplicated": self.jobs_duplicated,
            "status": self.status,
            "error_message": self.error_message,
            # SDD §I.2
            "report_id": self.report_id,
            "language_target": self.language_target,
            "passed_quality_gate": self.passed_quality_gate,
            "failure_reasons": dict(self.failure_reasons),
            "avg_enrichment_ms": float(self.avg_enrichment_ms),
            "errors": list(self.errors[:_MAX_ERRORS]),
            "quality_score": float(self.quality_score),
            "connector_crashed": bool(self.connector_crashed),
            "crash_reason": self.crash_reason,
            "soft_404_count": int(self.soft_404_count),
            "url_invalid_count": int(self.url_invalid_count),
        }


class ImportRunTracker:
    """Persists ImportRunRecord and exposes auto-disable inspection helpers."""

    def __init__(self, db: pymongo.MongoClient) -> None:  # type: ignore[type-arg]
        self._col: Collection = db[_COLLECTION]  # type: ignore[type-arg]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        # Name the indexes to match the legacy/Prisma convention so create_index
        # is idempotent across deploys. Tolerate IndexOptionsConflict (code 85)
        # when an equivalent index already exists under a different name.
        wanted = [
            (
                [("provider_name", pymongo.ASCENDING), ("started_at", pymongo.DESCENDING)],
                "import_runs_provider_name_started_at_idx",
                {},
            ),
            # SDD §I.2 — index supporting `import_reports` $lookup joins.
            (
                [("report_id", pymongo.ASCENDING)],
                "import_runs_report_id_idx",
                {},
            ),
        ]
        for keys, name, opts in wanted:
            try:
                self._col.create_index(keys, name=name, **opts)
            except OperationFailure as exc:
                if exc.code == 85:  # IndexOptionsConflict: equivalent index exists
                    log.warning(
                        "import_runs.index_already_present_with_other_name",
                        keys=keys,
                        wanted_name=name,
                        error=str(exc),
                    )
                else:
                    raise

    def save(self, record: ImportRunRecord) -> None:
        try:
            self._col.insert_one(record.to_doc())
        except Exception as exc:
            log.error("import_run.save_failed", error=str(exc))

    def should_disable(self, provider_name: str) -> bool:
        """Return True if last N runs all failed (auto-disable threshold)."""
        recent = list(
            self._col.find(
                {"provider_name": provider_name},
                sort=[("started_at", pymongo.DESCENDING)],
                limit=_MAX_CONSECUTIVE_FAILURES,
            )
        )
        if len(recent) < _MAX_CONSECUTIVE_FAILURES:
            return False
        return all(r["status"] == "failed" for r in recent)

    def consecutive_failures(self, provider_name: str) -> int:
        """Return count of consecutive failures for a provider."""
        count = 0
        for doc in self._col.find(
            {"provider_name": provider_name},
            sort=[("started_at", pymongo.DESCENDING)],
            limit=_MAX_CONSECUTIVE_FAILURES,
        ):
            if doc["status"] == "failed":
                count += 1
            else:
                break
        return count
