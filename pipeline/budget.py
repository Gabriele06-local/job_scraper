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
_ADZUNA_DAILY_LIMIT = 240


def _today_key() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


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
