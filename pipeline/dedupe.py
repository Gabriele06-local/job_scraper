"""Dedupe stage — three-layer duplicate detection (SPEC 04 §2).

Layer 1: URL exact match (DB unique index → DuplicateKeyError on insert).
Layer 2: dedup_hash exact match (DB unique index → DuplicateKeyError on insert).
Layer 2b: cross_source_hash match across different sources.
Layer 3: Fuzzy title match across sources, 14-day window.

This module handles L2/L2b/L3 app-level checks. L1 is handled by MongoDB on insert.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import structlog
from pymongo.collection import Collection
from rapidfuzz import fuzz

from models.job import Job, RawJob, compute_cross_source_hash, compute_dedup_hash

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

_FUZZY_THRESHOLD = 92
_FUZZY_WINDOW_DAYS = 14

# Re-export so callers can import from one place.
__all__ = [
    "compute_dedup_hash",
    "compute_cross_source_hash",
    "find_existing_job",
    "find_cross_source_dup",
    "merge_with_existing",
    "merge_cross_source",
    "check_fuzzy_dup",
]


def find_existing_job(dedup_hash: str, jobs_col: Collection) -> Optional[Job]:  # type: ignore[type-arg]
    """Return existing Job by dedup_hash, or None."""
    doc = jobs_col.find_one({"dedup_hash": dedup_hash})
    if doc is None:
        return None
    return Job.from_mongo_doc(doc)


def merge_with_existing(existing: Job, new_raw: RawJob, jobs_col: Collection) -> Job:  # type: ignore[type-arg]
    """Update existing job on dedupe hit per SPEC 04 §2.4.

    Refreshes last_seen_at, seen_count. Keeps earliest posted_at.
    Original URL preserved — new URL only appended to seen_count log.
    AI fields, status, reject_reason NEVER overwritten.
    """
    now = datetime.now(tz=timezone.utc)

    update: dict = {
        "$set": {"last_seen_at": now, "updated_at": now},
        "$inc": {"seen_count": 1},
    }

    # Keep earliest posted_at (normalize to UTC to avoid naive/aware comparison)
    def _utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    if (
        new_raw.posted_at
        and existing.posted_at
        and _utc(new_raw.posted_at) < _utc(existing.posted_at)
    ):
        update["$set"]["posted_at"] = new_raw.posted_at
        update["$set"]["published_at"] = new_raw.posted_at  # Bun compat

    jobs_col.update_one({"dedup_hash": existing.dedup_hash}, update)

    logger.info(
        "dedupe.hit",
        dedup_hash=existing.dedup_hash,
        existing_url=existing.url,
        new_url=new_raw.url,
        same_url=existing.url == new_raw.url,
    )

    # Refresh the in-memory object to reflect update
    existing.last_seen_at = now
    existing.updated_at = now
    if new_raw.posted_at and _utc(new_raw.posted_at) < _utc(existing.posted_at):
        existing.posted_at = new_raw.posted_at

    return existing


def find_cross_source_dup(  # type: ignore[type-arg]
    cross_hash: str,
    source: str,
    jobs_col: Collection,
) -> Optional[Job]:
    """Find existing job from a different source with the same cross_source_hash.

    Returns the existing Job or None. Uses the hash computed from
    title+company (without source) to catch the same offer on different boards.
    """
    doc = jobs_col.find_one(
        {
            "cross_source_hash": cross_hash,
            "source": {"$ne": source},
        },
    )
    if doc is None:
        return None
    return Job.from_mongo_doc(doc)


def merge_cross_source(  # type: ignore[type-arg]
    existing: Job,
    new_raw: RawJob,
    jobs_col: Collection,
) -> Job:
    """Merge cross-source duplicate, enriching the existing job.

    Keeps: longer description, wider salary range, earliest posted_at.
    Does NOT overwrite AI fields, status, or reject_reason.
    """
    now = datetime.now(tz=timezone.utc)
    set_fields: dict = {"last_seen_at": now, "updated_at": now}

    # Keep longer description
    if len(new_raw.description) > len(existing.content.description):
        set_fields["description"] = new_raw.description

    # Widen salary range
    if new_raw.salary_min is not None and (
        existing.salary.min is None or new_raw.salary_min < existing.salary.min
    ):
        set_fields["salary_min"] = new_raw.salary_min
    if new_raw.salary_max is not None and (
        existing.salary.max is None or new_raw.salary_max > existing.salary.max
    ):
        set_fields["salary_max"] = new_raw.salary_max

    # Keep earliest posted_at
    def _utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    if (
        new_raw.posted_at
        and existing.posted_at
        and _utc(new_raw.posted_at) < _utc(existing.posted_at)
    ):
        set_fields["posted_at"] = new_raw.posted_at
        set_fields["published_at"] = new_raw.posted_at

    update: dict = {"$set": set_fields, "$inc": {"seen_count": 1}}
    jobs_col.update_one({"dedup_hash": existing.dedup_hash}, update)

    logger.info(
        "dedupe.cross_source_hit",
        existing_url=existing.url,
        new_url=new_raw.url,
        source=new_raw.source,
    )

    existing.last_seen_at = now
    existing.updated_at = now
    if len(new_raw.description) > len(existing.content.description):
        existing.content.description = new_raw.description
    if new_raw.salary_min is not None and (
        existing.salary.min is None or new_raw.salary_min < existing.salary.min
    ):
        existing.salary.min = new_raw.salary_min
    if new_raw.salary_max is not None and (
        existing.salary.max is None or new_raw.salary_max > existing.salary.max
    ):
        existing.salary.max = new_raw.salary_max
    if (
        new_raw.posted_at
        and existing.posted_at
        and _utc(new_raw.posted_at) < _utc(existing.posted_at)
    ):
        existing.posted_at = new_raw.posted_at

    return existing


def check_fuzzy_dup(raw_job: RawJob, jobs_col: Collection) -> Optional[str]:  # type: ignore[type-arg]
    """Check for fuzzy title duplicate from a different source (SPEC 04 §2.3).

    Returns the _id string of the matching doc, or None.
    Uses rapidfuzz token_sort_ratio >= 92 within 14-day window, same company.
    """
    window_start = datetime.now(tz=timezone.utc) - timedelta(days=_FUZZY_WINDOW_DAYS)
    title_norm = raw_job.title.lower().strip()
    company_norm = raw_job.company_name.lower().strip()

    candidates = jobs_col.find(
        {
            "company.name_normalized": company_norm,
            "posted_at": {"$gte": window_start},
            "source": {"$ne": raw_job.source},
        },
        {"_id": 1, "title_normalized": 1},
    )

    for doc in candidates:
        score = fuzz.token_sort_ratio(title_norm, doc.get("title_normalized", ""))
        if score >= _FUZZY_THRESHOLD:
            logger.info(
                "dedupe.fuzzy_hit",
                title=raw_job.title[:80],
                company=raw_job.company_name,
                source=raw_job.source,
                matched_id=str(doc["_id"]),
                score=score,
            )
            return str(doc["_id"])

    return None
