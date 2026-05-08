"""Import run tracking — persists per-provider run records to MongoDB.

Collection: import_runs
Auto-disable logic: 3 consecutive failures → sets REGISTRY entry disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pymongo
import structlog
from pymongo.collection import Collection

log = structlog.get_logger(__name__)

_COLLECTION = "import_runs"
_MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class ImportRunRecord:
    provider_name: str
    started_at: datetime
    completed_at: datetime | None = None
    jobs_fetched: int = 0
    jobs_stored: int = 0
    jobs_discarded: int = 0
    jobs_duplicated: int = 0
    status: Literal["success", "partial", "failed"] = "failed"
    error_message: str | None = None

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
        }


class ImportRunTracker:
    """Persists ImportRunRecord to MongoDB and checks auto-disable threshold."""

    def __init__(self, db: pymongo.MongoClient) -> None:  # type: ignore[type-arg]
        self._col: Collection = db[_COLLECTION]  # type: ignore[type-arg]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self._col.create_index(
            [("provider_name", pymongo.ASCENDING), ("started_at", pymongo.DESCENDING)]
        )

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
