"""CLI entry points for the import service.

Usage:
    python -m import_service.cli import   [--dry-run] [--limit N]
    python -m import_service.cli expire   [--dry-run] [--limit N] [--max-age-days N]
    python -m import_service.cli geocode  [--dry-run] [--limit N]
    python -m import_service.cli reindex
    python -m import_service.cli stats
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from config import settings
from database.repository import disable_provider, ensure_indexes, get_jobs
from models.job import RawJob
from utils.text_fixer import fix_mojibake

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Normalizer: connector dict → RawJob
# ---------------------------------------------------------------------------


def _dict_to_raw_job(d: dict) -> RawJob | None:  # type: ignore[type-arg]
    """Convert a canonical connector dict to a RawJob.

    Every connector MUST return dicts with keys matching :class:`CanonicalJob`
    (see ``connectors/schema.py``). This function assumes the upstream has
    already normalised field names — no fallback chains for legacy keys.
    """

    url: str = d.get("url") or ""
    title: str = fix_mojibake(d.get("title") or "")
    description: str = fix_mojibake(d.get("description") or "")
    company_name: str = fix_mojibake(d.get("company_name") or "")
    source: str = d.get("source") or ""

    if not (url and title and company_name and source):
        return None

    posted_raw = d.get("published_at") or d.get("posted_at")
    posted_at: datetime | None = None
    if isinstance(posted_raw, datetime):
        posted_at = posted_raw if posted_raw.tzinfo else posted_raw.replace(tzinfo=timezone.utc)
    elif isinstance(posted_raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                posted_at = datetime.strptime(posted_raw, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

    location_raw: str | None = d.get("location") or d.get("location_raw")
    if location_raw:
        location_raw = fix_mojibake(location_raw)

    return RawJob(
        url=url,
        title=title,
        description=description,
        company_name=company_name,
        source=source,
        posted_at=posted_at,
        location_raw=location_raw,
        salary_min=d.get("salary_min"),
        salary_max=d.get("salary_max"),
        currency=d.get("currency"),
        original_language=d.get("language") or d.get("original_language"),
        external_id=d.get("external_id") or d.get("id"),
    )


# ---------------------------------------------------------------------------
# Command: import
# ---------------------------------------------------------------------------


def cmd_import(args: argparse.Namespace) -> int:
    """Full pipeline: fetch → normalize → URL validate → AI classify → persist.

    Per-connector crashes (uncaught exceptions in `connector.fetch()`) are
    swallowed *here* (not in the pipeline) so a single broken source doesn't
    abort the run; the failure is recorded on the per-source `ImportRunRecord`
    via `connector_crashed=True` + `crash_reason=...` and flows into the
    parent `import_reports` doc (SDD §A.6 / §I.2).
    """
    from connectors import get_enabled_connectors
    from connectors.active_jobs_db import ActiveJobsDbConnector
    from connectors.adzuna import AdzunaConnector
    from connectors.startup_jobs import StartupJobsConnector
    from connectors.workday_jobs import WorkdayJobsConnector
    from database.repository import get_companies, get_db
    from pipeline.budget import DailyBudget, MonthlyJobBudget
    from pipeline.import_run import ImportRunRecord, ImportRunTracker
    from pipeline.mojibake_migration import ensure_done as ensure_mojibake_done
    from pipeline.orchestrator import ImportPipeline
    from pipeline.report import ImportReportTracker
    from pipeline.url_validator import build_default as build_url_validator

    ensure_indexes()
    # One-shot self-heal: scan jobs/companies for double-encoded UTF-8
    # (e.g. "Ã©" → "é") on first boot in each env. Tracked in
    # `db.migrations`, so subsequent imports skip immediately.
    if not args.dry_run:
        ensure_mojibake_done()
    db = get_db()
    jobs_col = get_jobs()
    companies_col = get_companies()
    tracker = ImportRunTracker(db)
    report_tracker = ImportReportTracker(db)

    report_id: str | None = None
    if not args.dry_run:
        report_id = report_tracker.start(
            ai_model=settings.groq_model,
            language_targets=list(settings.scrape_languages),
            triggered_by="cli",
        )
        log.info("cli.import.report_started", report_id=report_id)

    pipeline = ImportPipeline(
        jobs_col=jobs_col,
        companies_col=companies_col,
        dry_run=args.dry_run,
        report_tracker=report_tracker if report_id else None,
        report_id=report_id,
    )

    connectors = get_enabled_connectors()

    # Optional --connectors filter (comma-separated names)
    only_names: set[str] = set()
    if getattr(args, "connectors", None):
        only_names = {n.strip().lower() for n in args.connectors.split(",") if n.strip()}
    if only_names:
        connectors = [c for c in connectors if c.source_name.lower() in only_names]
        log.info("cli.import.connector_filter", selected=sorted(only_names))

    # Inject call/job budgets now that DB is ready. Adzuna meters daily calls;
    # the Fantastic.Jobs RapidAPI sources meter jobs returned per month.
    monthly_budget_slugs = {
        ActiveJobsDbConnector: "active_jobs_db",
        WorkdayJobsConnector: "workday_jobs",
        StartupJobsConnector: "startup_jobs",
    }
    for connector in connectors:
        if isinstance(connector, AdzunaConnector):
            connector.set_budget(DailyBudget(db, "adzuna"))
            continue
        for cls, slug in monthly_budget_slugs.items():
            if isinstance(connector, cls):
                connector.set_budget(
                    MonthlyJobBudget(db, slug, limit=settings.rapidapi_monthly_job_budget)
                )
                break

    limit_per = getattr(args, "limit_per_connector", 0) or 0
    log.info(
        "cli.import.start",
        connectors=len(connectors),
        dry_run=args.dry_run,
        limit_per_connector=limit_per or "all",
    )

    all_raw: list[RawJob] = []
    per_source_raw: dict[str, list[RawJob]] = {}
    pending_records: list[ImportRunRecord] = []

    for connector in connectors:
        name = connector.source_name
        started_at = datetime.now(tz=timezone.utc)
        fetched = 0
        normalized = 0
        run_status: str = "success"
        error_msg: str | None = None
        connector_crashed = False
        crash_reason: str | None = None
        log.info("connector.fetch_start", connector=name)
        try:
            for raw_dict in connector.fetch():
                fetched += 1
                raw_job = _dict_to_raw_job(raw_dict)
                if raw_job is not None:
                    all_raw.append(raw_job)
                    per_source_raw.setdefault(name, []).append(raw_job)
                    normalized += 1
                    if limit_per and normalized >= limit_per:
                        break
        except Exception as exc:  # noqa: BLE001 — wide catch is intentional
            # SDD §A.6: a single broken connector must NOT abort the run.
            run_status = "failed"
            connector_crashed = True
            error_msg = str(exc)
            crash_reason = repr(exc)[:500]
            log.error("cli.import.connector_error", connector=name, error=exc)
        finally:
            log.info(
                "connector.fetch_done",
                connector=name,
                fetched_raw=fetched,
                normalized=normalized,
                status=run_status,
            )
            record = ImportRunRecord(
                provider_name=name,
                provider_slug=getattr(connector, "slug", "") or None,
                started_at=started_at,
                completed_at=datetime.now(tz=timezone.utc),
                jobs_fetched=fetched,
                status=run_status,  # type: ignore[arg-type]
                error_message=error_msg,
                report_id=report_id,
                connector_crashed=connector_crashed,
                crash_reason=crash_reason,
            )
            pending_records.append(record)
            # Continue to next connector regardless of crash status.

    if args.limit:
        all_raw = all_raw[: args.limit]

    log.info("cli.import.fetched", count=len(all_raw))

    # Stage 0.5 (SDD §A.1): pre-pipeline URL probe over the union of all
    # normalized URLs. Skipped on dry-run to avoid hammering external hosts.
    url_results = {}
    if not args.dry_run and all_raw:
        validator = build_url_validator()
        try:
            url_results = asyncio.run(validator.validate_many([j.url for j in all_raw]))
        except Exception as exc:  # noqa: BLE001 — network code is fragile
            log.warning("cli.import.url_validator_failed", error=str(exc))
            url_results = {}

    result = pipeline.run(all_raw, url_results=url_results)
    c = result.counters

    # Reconcile per-connector aggregates from the union pipeline result.
    # We can't fully break down counters per connector without re-running,
    # so we attribute URL-invalid + quality stats proportionally via the
    # `per_source_raw` mapping where possible.
    for record in pending_records:
        connector_raw = per_source_raw.get(record.provider_name, [])
        connector_urls = {r.url for r in connector_raw}
        record.url_invalid_count = sum(
            1 for u, r in url_results.items() if u in connector_urls and not r.is_valid
        )
        # Best-effort jobs_stored attribution from per-source raw counts:
        # the per-connector slice can't tell which were rejected vs persisted,
        # so we report `len(connector_raw)` as the fetched-normalized count and
        # the aggregates as-fetched. Detailed per-source counters require a
        # follow-up SDD ticket.
        record.jobs_stored = len(connector_raw) if not args.dry_run else 0

    # Save per-source records (and roll them up into the parent report).
    if not args.dry_run:
        for record in pending_records:
            tracker.save(record)
            if tracker.should_disable(record.provider_name):
                consecutive = tracker.consecutive_failures(record.provider_name)
                disable_provider(
                    record.provider_name,
                    reason=f"Auto-disabled after {consecutive} consecutive import failures",
                )
                log.warning(
                    "cli.import.provider_disabled",
                    provider=record.provider_name,
                    consecutive=consecutive,
                )
            if report_id:
                report_tracker.add_source(report_id, record)

    if report_id:
        report_tracker.finish(report_id)

    log.info(
        "cli.import.done",
        total=c.total,
        persisted=c.persisted,
        gate_valid=c.gate_valid,
        gate_premium=c.gate_premium,
        gate_rejected=c.gate_rejected,
        prefilter_rejected=c.prefilter_rejected,
        url_invalid=c.url_invalid,
        dedupe_hit=c.dedupe_hit,
        ai_unavailable=c.ai_unavailable,
        geocode_pending=c.geocode_pending,
        avg_enrichment_ms=round(c.average_enrichment_ms(), 1),
        report_id=report_id,
    )
    _write_health(command="import", counters={"persisted": c.persisted, "total": c.total})
    return 0


# ---------------------------------------------------------------------------
# Command: expire
# ---------------------------------------------------------------------------


def cmd_expire(args: argparse.Namespace) -> int:
    """Expiration probe: HEAD/GET active jobs, mark dead ones expired."""
    from pipeline.expiration import ExpirationChecker

    ensure_indexes()
    jobs_col = get_jobs()
    checker = ExpirationChecker(jobs_col=jobs_col, dry_run=args.dry_run)

    result = asyncio.run(
        checker.run(
            limit=args.limit or None,
            max_age_days=args.max_age_days or None,
        )
    )
    c = result.counters
    log.info(
        "cli.expire.done",
        total=c.total,
        probed=c.probed,
        expired=c.expired,
        transient=c.transient,
        alive=c.alive,
        error=c.error,
        max_age_expired=c.max_age_expired,
    )
    _write_health(
        command="expire",
        counters={"expired": c.expired, "probed": c.probed},
    )
    return 0


# ---------------------------------------------------------------------------
# Command: geocode (SDD §A.8)
# ---------------------------------------------------------------------------


def cmd_geocode(args: argparse.Namespace) -> int:
    """Backfill location.geo on jobs with `quality.geocode_pending=True`."""
    from pipeline.geocoder import NominatimGeocoder

    ensure_indexes()
    jobs_col = get_jobs()

    limit = args.limit or 50

    cursor = jobs_col.find(
        {
            "$or": [
                {"quality.geocode_pending": True},
                {"geocode_pending": True},
            ],
            "location_raw": {"$ne": None},
        },
        limit=limit,
    )
    candidates = list(cursor)
    log.info("cli.geocode.start", candidates=len(candidates), dry_run=args.dry_run)

    if not candidates:
        _write_health(command="geocode", counters={"processed": 0, "updated": 0})
        return 0

    geocoder = NominatimGeocoder()

    async def _run() -> tuple[int, int]:
        processed = 0
        updated = 0
        for doc in candidates:
            processed += 1
            address = doc.get("location_raw") or doc.get("location")
            if not address:
                continue
            hit = await geocoder.lookup(address)
            if hit is None:
                continue
            if args.dry_run:
                updated += 1
                continue
            now = datetime.now(tz=timezone.utc)
            jobs_col.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "location_geo": {
                            "type": "Point",
                            "coordinates": [hit.lng, hit.lat],
                        },
                        "formatted_address": hit.formatted_address,
                        "city": hit.city or doc.get("city"),
                        "country": hit.country or doc.get("country"),
                        "quality.geocode_pending": False,
                        "geocode_pending": False,
                        "updated_at": now,
                    },
                    "$unset": {},
                },
            )
            updated += 1
        return processed, updated

    processed, updated = asyncio.run(_run())
    log.info("cli.geocode.done", processed=processed, updated=updated, dry_run=args.dry_run)
    _write_health(
        command="geocode",
        counters={"processed": processed, "updated": updated},
    )
    return 0


# ---------------------------------------------------------------------------
# Command: reindex
# ---------------------------------------------------------------------------


def cmd_reindex(_args: argparse.Namespace) -> int:
    """Rebuild all MongoDB indexes (idempotent, fail-loud).

    Also touches `import_reports` so its three indexes are present even when
    no `import` run has fired yet (SDD §D.2).
    """
    from database.repository import get_db
    from pipeline.import_run import ImportRunTracker
    from pipeline.report import ImportReportTracker

    log.info("cli.reindex.start")
    ensure_indexes()
    db = get_db()
    ImportRunTracker(db)  # constructor materializes indexes
    ImportReportTracker(db)  # ditto
    log.info("cli.reindex.done")
    _write_health(command="reindex", counters={})
    return 0


# ---------------------------------------------------------------------------
# Command: stats
# ---------------------------------------------------------------------------


def cmd_stats(_args: argparse.Namespace) -> int:
    """Print job collection metrics to stdout (uses SDD §I.4 status vocab)."""
    ensure_indexes()
    col = get_jobs()
    now = datetime.now(tz=timezone.utc)

    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    status_counts: dict[str, int] = {}
    for row in col.aggregate(pipeline):
        status_counts[row["_id"] or "null"] = row["count"]

    total = col.count_documents({})
    # SDD §D.1: "active" replaces legacy "valid"/"premium" — count both
    # vocabularies for backward-compat with un-migrated databases.
    active = col.count_documents({"status": {"$in": ["active", "valid", "premium"]}})
    expired = col.count_documents({"status": "expired"})
    never_probed = col.count_documents(
        {"status": {"$in": ["active", "valid", "premium"]}, "last_probed_at": None}
    )

    stats = {
        "as_of": now.isoformat(),
        "total_jobs": total,
        "active": active,
        "expired": expired,
        "never_probed": never_probed,
        "by_status": status_counts,
    }
    print(json.dumps(stats, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Health file writer
# ---------------------------------------------------------------------------


def _write_health(command: str, counters: dict) -> None:  # type: ignore[type-arg]
    path = Path(settings.health_check_file)
    try:
        payload = {
            "last_command": command,
            "last_run_at": datetime.now(tz=timezone.utc).isoformat(),
            "counters": counters,
        }
        path.write_text(json.dumps(payload))
    except OSError as exc:
        log.warning("cli.health_write_failed", path=str(path), error=str(exc))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m import_service.cli",
        description="DevBoards import service CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # import
    p_import = sub.add_parser("import", help="Run full fetch + classify + persist pipeline")
    p_import.add_argument("--dry-run", action="store_true", help="Skip all DB writes")
    p_import.add_argument("--limit", type=int, default=0, help="Max jobs to process (0=all)")
    p_import.add_argument(
        "--limit-per-connector",
        type=int,
        default=0,
        help="Max normalized jobs per connector (0=all)",
    )
    p_import.add_argument(
        "--connectors",
        type=str,
        default="",
        help="Comma-separated connector names to include (default: all enabled)",
    )

    # expire
    p_expire = sub.add_parser("expire", help="Probe active job URLs, mark expired ones")
    p_expire.add_argument("--dry-run", action="store_true", help="Skip all DB writes")
    p_expire.add_argument("--limit", type=int, default=0, help="Max jobs to probe (0=use config)")
    p_expire.add_argument(
        "--max-age-days",
        type=int,
        default=0,
        help="Jobs older than this without a probe are force-expired (0=use config)",
    )

    # geocode
    p_geo = sub.add_parser(
        "geocode",
        help="Backfill location.geo for jobs with quality.geocode_pending=True",
    )
    p_geo.add_argument("--dry-run", action="store_true", help="Skip all DB writes")
    p_geo.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max pending jobs to process per run (default 50)",
    )

    # reindex
    sub.add_parser("reindex", help="Rebuild all MongoDB indexes")

    # stats
    sub.add_parser("stats", help="Print collection metrics as JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "import": cmd_import,
        "expire": cmd_expire,
        "geocode": cmd_geocode,
        "reindex": cmd_reindex,
        "stats": cmd_stats,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
