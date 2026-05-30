"""Adzuna daily call budget — prevents exceeding 240 API calls/day.

Collection: import_budgets
Key: provider name + UTC date string (YYYY-MM-DD).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pymongo
import structlog
from pymongo.collection import Collection

log = structlog.get_logger(__name__)

_COLLECTION = "import_budgets"
_MONTHLY_COLLECTION = "import_budgets_monthly"
_ADZUNA_DAILY_LIMIT = 240
_FANTASTIC_JOBS_MONTHLY_LIMIT = 250


def _today_key() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _month_key() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m")


class DailyBudget:
    """Read/write call counter in MongoDB for a given provider+date."""

    def __init__(self, db: pymongo.MongoClient, provider: str) -> None:  # type: ignore[type-arg]
        self._col: Collection = db[_COLLECTION]  # type: ignore[type-arg]
        self._provider = provider
        self._col.create_index(
            [("provider", pymongo.ASCENDING), ("date", pymongo.ASCENDING)],
            unique=True,
        )

    def _doc_id(self) -> dict:
        return {"provider": self._provider, "date": _today_key()}

    def increment(self) -> int:
        """Increment call count and return the new value."""
        result = self._col.find_one_and_update(
            self._doc_id(),
            {"$inc": {"calls": 1}},
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER,
        )
        count: int = result["calls"]
        return count

    def current(self) -> int:
        """Return today's call count without incrementing."""
        doc = self._col.find_one(self._doc_id())
        return doc["calls"] if doc else 0

    def is_exhausted(self, limit: int = _ADZUNA_DAILY_LIMIT) -> bool:
        return self.current() >= limit

    def consume(self, limit: int = _ADZUNA_DAILY_LIMIT) -> bool:
        """Increment and return True if still within budget, False if over limit."""
        count = self.increment()
        if count > limit:
            log.warning(
                "budget.exhausted",
                provider=self._provider,
                count=count,
                limit=limit,
            )
            return False
        return True


class MonthlyJobBudget:
    """Track jobs consumed per provider per UTC month.

    For RapidAPI jobs-metered plans (Fantastic.Jobs APIs bill per job returned,
    not per request). Stored in a dedicated collection keyed by provider+month
    so it never collides with `DailyBudget`'s unique (provider, date) index.
    `limit <= 0` means uncapped.
    """

    def __init__(
        self,
        db: pymongo.MongoClient,  # type: ignore[type-arg]
        provider: str,
        limit: int = _FANTASTIC_JOBS_MONTHLY_LIMIT,
    ) -> None:
        self._col: Collection = db[_MONTHLY_COLLECTION]  # type: ignore[type-arg]
        self._provider = provider
        self._limit = limit
        self._col.create_index(
            [("provider", pymongo.ASCENDING), ("month", pymongo.ASCENDING)],
            unique=True,
        )

    def _doc_id(self) -> dict:  # type: ignore[type-arg]
        return {"provider": self._provider, "month": _month_key()}

    def current(self) -> int:
        """Return jobs consumed this month without modifying state."""
        doc = self._col.find_one(self._doc_id())
        return doc["jobs"] if doc else 0

    def remaining(self) -> int:
        """Jobs still allowed this month (a large number when uncapped)."""
        if self._limit <= 0:
            return 1_000_000_000
        return max(0, self._limit - self.current())

    def is_exhausted(self) -> bool:
        return self._limit > 0 and self.current() >= self._limit

    def add(self, jobs: int) -> int:
        """Record `jobs` consumed this month; return the new monthly total."""
        if jobs <= 0:
            return self.current()
        result = self._col.find_one_and_update(
            self._doc_id(),
            {"$inc": {"jobs": jobs}},
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER,
        )
        total: int = result["jobs"]
        return total
