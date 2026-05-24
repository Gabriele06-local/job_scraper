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


def get_providers() -> Collection:  # type: ignore[type-arg]
    """Typed accessor for the providers collection (backoffice enable/disable)."""
    return get_db()["providers"]


# Slugs that were enabled in code before the DB-backed provider gate landed.
# Used as a fallback in `is_provider_enabled` so that an un-seeded DB does
# NOT silently disable the whole pipeline. New providers (added after the
# `providers` collection was introduced) are NOT in this list — they must
# be explicitly enabled via the backoffice / seed script.
_LEGACY_ENABLED_SLUGS: frozenset[str] = frozenset(
    {
        "adzuna",
        "arbeitnow",
        "ashby",
        "greenhouse",
        "himalayas",
        "iprogrammatori",
        "jobicy",
        "jooble",
        "jsearch",
        "lever",
        "personio",
        "reed",
        "remoteok",
        "remotive",
        "rss",
        "themuse",
    }
)


def is_provider_enabled(slug: str) -> bool:
    """Return True if the provider is enabled in the DB.

    Consult `providers` collection first; if no document exists for `slug`,
    fall back to the legacy whitelist so pre-existing connectors keep working
    on un-seeded environments. New connectors default to disabled.
    Any DB error degrades to the same fallback (logged at WARNING).
    """
    try:
        doc = get_providers().find_one({"slug": slug}, {"enabled": 1})
    except Exception as exc:  # noqa: BLE001 — DB-level fallback is intentional
        logger.warning("providers.lookup_failed", slug=slug, error=str(exc))
        return slug in _LEGACY_ENABLED_SLUGS
    if doc is None:
        return slug in _LEGACY_ENABLED_SLUGS
    return bool(doc.get("enabled", False))


def ensure_indexes() -> None:
    """Create all required indexes idempotently.

    Raises OperationFailure on conflict — does NOT swallow errors.
    Call once at pipeline boot.
    """
    db = get_db()
    _ensure_jobs_indexes(db)
    _ensure_companies_indexes(db)
    _ensure_providers_indexes(db)
    logger.info("mongo.indexes_ready")


def _dedupe_field(coll: Collection, field: str) -> int:  # type: ignore[type-arg]
    """Drop duplicate docs on `field`, keeping the newest by _id.

    Required before creating a unique index: build fails on existing dups.
    Returns the number of docs deleted.
    """
    cursor = coll.aggregate(
        [
            {"$match": {field: {"$exists": True, "$ne": None}}},
            {"$sort": {"_id": -1}},
            {"$group": {"_id": f"${field}", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ],
        allowDiskUse=True,
    )
    ids_to_delete: list = []
    for group in cursor:
        # Keep first (most recent), drop the rest.
        ids_to_delete.extend(group["ids"][1:])
    if not ids_to_delete:
        return 0
    result = coll.delete_many({"_id": {"$in": ids_to_delete}})
    logger.warning(
        "mongo.dedupe_before_index",
        field=field,
        deleted=result.deleted_count,
    )
    return result.deleted_count


def _ensure_jobs_indexes(db: Database) -> None:  # type: ignore[type-arg]
    jobs = db["jobs"]
    indexes = [
        # sparse=True so backend-managed docs (no url/dedup_hash field) are excluded
        IndexModel([("url", ASCENDING)], unique=True, sparse=True, name="url_unique"),
        IndexModel([("dedup_hash", ASCENDING)], unique=True, sparse=True, name="dedup_hash_unique"),
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
        IndexModel([("last_probed_at", ASCENDING)], sparse=True, name="last_probed_at_sparse"),
        IndexModel([("location.geo", "2dsphere")], name="location_geo_2dsphere"),
        IndexModel([("company.name_normalized", ASCENDING)], name="company_name_normalized"),
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
        # Drop indexes that need option changes (sparse, language_override)
        existing = {idx["name"] for idx in jobs.list_indexes()}
        for name in ("url_unique", "dedup_hash_unique", "text_index"):
            if name in existing:
                jobs.drop_index(name)
        # Unique-index build aborts on pre-existing dups (see prod failure
        # 2026-05-15: E11000 on url_unique). Dedupe first.
        _dedupe_field(jobs, "url")
        _dedupe_field(jobs, "dedup_hash")
        jobs.create_indexes(indexes)
        logger.info("mongo.jobs_indexes_created")
    except OperationFailure as e:
        logger.error("mongo.jobs_indexes_failed", error=str(e))
        raise


def _ensure_companies_indexes(db: Database) -> None:  # type: ignore[type-arg]
    companies = db["companies"]
    indexes = [
        IndexModel([("name", ASCENDING)], unique=True, name="name_unique"),
        # sparse=True so Prisma-managed documents (no name_normalized field) are excluded
        IndexModel(
            [("name_normalized", ASCENDING)],
            unique=True,
            sparse=True,
            name="name_normalized_unique",
        ),
    ]
    try:
        # Drop and recreate name_normalized_unique if options changed (e.g. sparse)
        existing = {idx["name"] for idx in companies.list_indexes()}
        if "name_normalized_unique" in existing:
            companies.drop_index("name_normalized_unique")
        _dedupe_field(companies, "name")
        _dedupe_field(companies, "name_normalized")
        companies.create_indexes(indexes)
        logger.info("mongo.companies_indexes_created")
    except OperationFailure as e:
        logger.error("mongo.companies_indexes_failed", error=str(e))
        raise


def _ensure_providers_indexes(db: Database) -> None:  # type: ignore[type-arg]
    providers = db["providers"]
    indexes = [
        IndexModel([("slug", ASCENDING)], unique=True, name="slug_unique"),
    ]
    try:
        _dedupe_field(providers, "slug")
        providers.create_indexes(indexes)
        logger.info("mongo.providers_indexes_created")
    except OperationFailure as e:
        logger.error("mongo.providers_indexes_failed", error=str(e))
        raise


def close_client() -> None:
    """Close the singleton client (used in tests and clean shutdown)."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
