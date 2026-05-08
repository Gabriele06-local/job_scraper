"""MongoDB connection singleton + idempotent index creation.

Fails loudly on index errors (fixes P1-01: silent index swallow).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import structlog
from pymongo import ASCENDING, DESCENDING, TEXT, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.operations import IndexModel

from config import settings

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

_client: MongoClient | None = None  # type: ignore[type-arg]
_lock = threading.Lock()


def get_client() -> MongoClient:  # type: ignore[type-arg]
    """Return the singleton pooled MongoClient (lazy init, thread-safe)."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = MongoClient(
                    settings.database_url,
                    maxPoolSize=20,
                    serverSelectionTimeoutMS=5000,
                )
                logger.info("mongo.connected", db=settings.mongo_db)
    return _client


def get_db() -> Database:  # type: ignore[type-arg]
    """Return the configured database."""
    return get_client()[settings.mongo_db]


def get_jobs() -> Collection:  # type: ignore[type-arg]
    """Typed accessor for the jobs collection."""
    return get_db()["jobs"]


def get_companies() -> Collection:  # type: ignore[type-arg]
    """Typed accessor for the companies collection."""
    return get_db()["companies"]


def get_seniorities() -> Collection:  # type: ignore[type-arg]
    """Typed accessor for the seniorities collection (legacy, kept for Bun compat)."""
    return get_db()["seniorities"]


def ensure_indexes() -> None:
    """Create all required indexes idempotently.

    Raises OperationFailure on conflict — does NOT swallow errors.
    Call once at pipeline boot.
    """
    db = get_db()
    _ensure_jobs_indexes(db)
    _ensure_companies_indexes(db)
    logger.info("mongo.indexes_ready")


def _ensure_jobs_indexes(db: Database) -> None:  # type: ignore[type-arg]
    jobs = db["jobs"]
    indexes = [
        IndexModel([("url", ASCENDING)], unique=True, name="url_unique"),
        IndexModel([("dedup_hash", ASCENDING)], unique=True, name="dedup_hash_unique"),
        IndexModel(
            [("status", ASCENDING), ("posted_at", DESCENDING)],
            name="status_posted_at",
        ),
        IndexModel(
            [("language", ASCENDING), ("status", ASCENDING), ("posted_at", DESCENDING)],
            name="language_status_posted_at",
        ),
        IndexModel([("source", ASCENDING)], name="source"),
        IndexModel([("expires_at", ASCENDING)], sparse=True, name="expires_at_sparse"),
        IndexModel(
            [("last_probed_at", ASCENDING)], sparse=True, name="last_probed_at_sparse"
        ),
        IndexModel(
            [("location.geo", "2dsphere")], name="location_geo_2dsphere"
        ),
        IndexModel(
            [("company.name_normalized", ASCENDING)], name="company_name_normalized"
        ),
        IndexModel(
            [("role_family", ASCENDING), ("seniority", ASCENDING)],
            name="role_family_seniority",
        ),
        IndexModel(
            [("title", TEXT), ("description", TEXT)],
            weights={"title": 5, "description": 1},
            default_language="english",
            language_override="lang_override",  # non-existent field → always use default_language
            name="text_index",
        ),
    ]
    try:
        # Drop text_index if it exists with different options (e.g. missing language_override)
        existing = {idx["name"] for idx in jobs.list_indexes()}
        if "text_index" in existing:
            jobs.drop_index("text_index")
        jobs.create_indexes(indexes)
        logger.info("mongo.jobs_indexes_created")
    except OperationFailure as e:
        logger.error("mongo.jobs_indexes_failed", error=str(e))
        raise


def _ensure_companies_indexes(db: Database) -> None:  # type: ignore[type-arg]
    companies = db["companies"]
    indexes = [
        IndexModel([("name", ASCENDING)], unique=True, name="name_unique"),
        IndexModel(
            [("name_normalized", ASCENDING)], unique=True, name="name_normalized_unique"
        ),
    ]
    try:
        companies.create_indexes(indexes)
        logger.info("mongo.companies_indexes_created")
    except OperationFailure as e:
        logger.error("mongo.companies_indexes_failed", error=str(e))
        raise


def close_client() -> None:
    """Close the singleton client (used in tests and clean shutdown)."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
