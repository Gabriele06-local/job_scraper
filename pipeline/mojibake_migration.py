"""Reusable migration: repair mojibake in jobs + companies.

Exposed so both the standalone CLI script (`scripts/fix_mojibake.py`)
and the auto-run hook in `import_service.cli.cmd_import` can share the
same implementation and tracking marker.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from database.repository import get_companies, get_db, get_jobs
from utils.text_fixer import fix_mojibake, has_mojibake

log = structlog.get_logger(__name__)

MIGRATION_SLUG = "fix_mojibake_2026_05"

_MOJIBAKE_REGEX = "Ã"


def _jobs_query() -> dict:
    return {
        "$or": [
            {"content.title": {"$regex": _MOJIBAKE_REGEX}},
            {"content.description": {"$regex": _MOJIBAKE_REGEX}},
            {"company.name": {"$regex": _MOJIBAKE_REGEX}},
            {"location.raw": {"$regex": _MOJIBAKE_REGEX}},
        ]
    }


def _companies_query() -> dict:
    return {"name": {"$regex": _MOJIBAKE_REGEX}}


def _fix_jobs(dry_run: bool) -> tuple[int, int]:
    col = get_jobs()
    scanned = 0
    fixed = 0

    for doc in col.find(_jobs_query()):
        scanned += 1
        update: dict[str, str] = {}

        content = doc.get("content") or {}
        title = content.get("title") or ""
        description = content.get("description") or ""
        if has_mojibake(title):
            new = fix_mojibake(title)
            if new != title:
                update["content.title"] = new
        if has_mojibake(description):
            new = fix_mojibake(description)
            if new != description:
                update["content.description"] = new

        company = doc.get("company") or {}
        cname = company.get("name") or ""
        if has_mojibake(cname):
            new = fix_mojibake(cname)
            if new != cname:
                update["company.name"] = new

        location = doc.get("location") or {}
        loc_raw = location.get("raw") or ""
        if has_mojibake(loc_raw):
            new = fix_mojibake(loc_raw)
            if new != loc_raw:
                update["location.raw"] = new

        if not update:
            continue
        fixed += 1
        if dry_run:
            log.info(
                "fix_mojibake.would_update",
                _id=str(doc.get("_id")),
                fields=list(update.keys()),
            )
        else:
            col.update_one({"_id": doc["_id"]}, {"$set": update})

    return scanned, fixed


def _fix_companies(dry_run: bool) -> tuple[int, int]:
    col = get_companies()
    scanned = 0
    fixed = 0

    for doc in col.find(_companies_query()):
        scanned += 1
        name = doc.get("name") or ""
        if not has_mojibake(name):
            continue
        new = fix_mojibake(name)
        if new == name:
            continue
        fixed += 1
        if dry_run:
            log.info(
                "fix_mojibake.would_update_company",
                _id=str(doc.get("_id")),
                old=name,
                new=new,
            )
        else:
            col.update_one({"_id": doc["_id"]}, {"$set": {"name": new}})

    return scanned, fixed


def is_done() -> bool:
    """Return True if the migration has already been recorded as completed."""
    try:
        return get_db()["migrations"].find_one({"slug": MIGRATION_SLUG}) is not None
    except Exception as exc:  # noqa: BLE001 — boot-time DB hiccup must not crash import
        log.warning("fix_mojibake.is_done_check_failed", error=str(exc))
        return False


def _mark_done() -> None:
    now = datetime.now(tz=timezone.utc)
    get_db()["migrations"].update_one(
        {"slug": MIGRATION_SLUG},
        {
            "$setOnInsert": {"slug": MIGRATION_SLUG, "created_at": now},
            "$set": {"completed_at": now},
        },
        upsert=True,
    )


def run(dry_run: bool, rerun: bool = False) -> tuple[int, int, int, int]:
    """Execute the migration.

    Returns: (jobs_scanned, jobs_fixed, companies_scanned, companies_fixed).
    """
    if not rerun and not dry_run and is_done():
        log.info("fix_mojibake.already_done", slug=MIGRATION_SLUG)
        return (0, 0, 0, 0)

    log.info("fix_mojibake.start", dry_run=dry_run)
    j_scanned, j_fixed = _fix_jobs(dry_run)
    c_scanned, c_fixed = _fix_companies(dry_run)
    log.info(
        "fix_mojibake.summary",
        jobs_scanned=j_scanned,
        jobs_fixed=j_fixed,
        companies_scanned=c_scanned,
        companies_fixed=c_fixed,
        dry_run=dry_run,
    )

    if not dry_run:
        _mark_done()
        log.info("fix_mojibake.marked_done", slug=MIGRATION_SLUG)

    return (j_scanned, j_fixed, c_scanned, c_fixed)


def ensure_done() -> None:
    """Auto-run hook called from the import boot path.

    Runs the migration once per environment if not already marked done.
    Failures are logged but never raised — they must not block an import
    pipeline that is otherwise healthy.
    """
    try:
        if is_done():
            return
        log.info("fix_mojibake.auto_run_start")
        run(dry_run=False, rerun=False)
    except Exception as exc:  # noqa: BLE001 — auto-run never blocks the import
        log.warning("fix_mojibake.auto_run_failed", error=str(exc))
