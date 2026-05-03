"""CLI entry points for the import service.

Usage:
    python -m import_service.cli import   [--dry-run] [--limit N]
    python -m import_service.cli expire   [--dry-run] [--limit N] [--max-age-days N]
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
from database.repository import ensure_indexes, get_jobs
from models.job import RawJob

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Normalizer: connector dict → RawJob
# ---------------------------------------------------------------------------


def _dict_to_raw_job(d: dict) -> RawJob | None:  # type: ignore[type-arg]
    """Convert a raw connector dict to a RawJob. Returns None if required fields missing."""

    url: str = d.get("url") or d.get("link") or ""
    title: str = d.get("title") or ""
    description: str = d.get("description") or ""

    company = d.get("company") or {}
    if isinstance(company, dict):
        company_name = company.get("name") or d.get("company_name") or ""
    else:
        company_name = str(company) if company else d.get("company_name") or ""

    source: str = d.get("source") or ""

    if not (url and title and company_name and source):
        return None

    # posted_at: accept datetime or ISO string
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
    """Full pipeline: fetch → normalize → pre-filter → AI classify → persist."""
    from connectors import get_enabled_connectors
    from pipeline.orchestrator import ImportPipeline

    ensure_indexes()
    jobs_col = get_jobs()
    pipeline = ImportPipeline(jobs_col=jobs_col, dry_run=args.dry_run)

    connectors = get_enabled_connectors()
    log.info("cli.import.start", connectors=len(connectors), dry_run=args.dry_run)

    all_raw = []
    for connector in connectors:
        name = connector.source_name
        try:
            for raw_dict in connector.fetch():
                raw_job = _dict_to_raw_job(raw_dict)
                if raw_job is not None:
                    all_raw.append(raw_job)
        except Exception as exc:
            log.error("cli.import.connector_error", connector=name, error=str(exc))

    if args.limit:
        all_raw = all_raw[: args.limit]

    log.info("cli.import.fetched", count=len(all_raw))
    result = pipeline.run(all_raw)
    c = result.counters
    log.info(
        "cli.import.done",
        total=c.total,
        persisted=c.persisted,
        gate_valid=c.gate_valid,
        gate_premium=c.gate_premium,
        gate_rejected=c.gate_rejected,
        prefilter_rejected=c.prefilter_rejected,
        dedupe_hit=c.dedupe_hit,
        ai_unavailable=c.ai_unavailable,
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
# Command: reindex
# ---------------------------------------------------------------------------


def cmd_reindex(_args: argparse.Namespace) -> int:
    """Rebuild all MongoDB indexes (idempotent, fail-loud)."""
    log.info("cli.reindex.start")
    ensure_indexes()
    log.info("cli.reindex.done")
    _write_health(command="reindex", counters={})
    return 0


# ---------------------------------------------------------------------------
# Command: stats
# ---------------------------------------------------------------------------


def cmd_stats(_args: argparse.Namespace) -> int:
    """Print job collection metrics to stdout."""
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
    active = col.count_documents({"status": {"$in": ["valid", "premium"]}})
    expired = col.count_documents({"status": "expired"})
    never_probed = col.count_documents(
        {"status": {"$in": ["valid", "premium"]}, "last_probed_at": None}
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
        "reindex": cmd_reindex,
        "stats": cmd_stats,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
