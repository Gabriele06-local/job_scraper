#!/usr/bin/env python3
"""Migration script — wipe & re-import (DESTRUCTIVE).

Default mode is --dry-run: fetches and pre-filters but writes nothing.
Pass --confirm to drop the jobs collection, recreate indexes, and run a
full re-import.

Usage:
    python scripts/migrate.py                    # dry-run (default)
    python scripts/migrate.py --dry-run          # explicit dry-run
    python scripts/migrate.py --confirm          # REAL EXECUTION

Idempotent: re-running after a failure is safe. Drop step is a no-op on
empty collection. ensure_indexes() is idempotent. Pipeline persistence
uses dedup_hash upsert.

Dry-run report path: docs/reports/03-migration-dryrun.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog  # noqa: E402

from config import settings  # noqa: E402
from connectors import REGISTRY, get_enabled_connectors  # noqa: E402
from database.repository import ensure_indexes, get_jobs  # noqa: E402
from import_service.cli import _dict_to_raw_job  # noqa: E402
from pipeline.prefilter import should_send_to_ai  # noqa: E402

log = structlog.get_logger(__name__)

# Cost baseline from docs/reports/01-ai-baseline.md (claude-05).
# Avg cost per Groq classify call across 30 ground-truth fixtures.
_AVG_COST_PER_CALL_USD: float = 0.0016 / 30  # ≈ $0.0000533

REPORT_PATH = Path(__file__).parent.parent / "docs" / "reports" / "03-migration-dryrun.md"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def _step(num: int, total: int, label: str) -> None:
    log.info("migrate.step", step=f"{num}/{total}", label=label)


def _fetch_and_prefilter(
    connectors: list,  # type: ignore[type-arg]
) -> tuple[
    dict[str, int],  # per_source counts
    dict[str, int],  # per_source rejected
    Counter,  # reject_reasons
    int,  # total raw
    int,  # total normalized
    int,  # total candidates (post-prefilter)
]:
    """Run fetch + normalize + prefilter without any DB writes."""
    per_source: dict[str, int] = {}
    per_source_rejected: dict[str, int] = {}
    reject_reasons: Counter = Counter()
    total_raw = 0
    total_normalized = 0
    total_candidates = 0

    for connector in connectors:
        name = connector.source_name
        per_source.setdefault(name, 0)
        per_source_rejected.setdefault(name, 0)

        try:
            for raw_dict in connector.fetch():
                total_raw += 1
                raw_job = _dict_to_raw_job(raw_dict)
                if raw_job is None:
                    per_source_rejected[name] += 1
                    reject_reasons["NORMALIZE_FAILED"] += 1
                    continue
                total_normalized += 1

                passes, reason = should_send_to_ai(raw_job)
                if not passes:
                    per_source_rejected[name] += 1
                    reject_reasons[reason or "UNKNOWN"] += 1
                    continue

                per_source[name] += 1
                total_candidates += 1
        except Exception as exc:
            log.error("migrate.connector_error", connector=name, error=str(exc))

    return (
        per_source,
        per_source_rejected,
        reject_reasons,
        total_raw,
        total_normalized,
        total_candidates,
    )


def _write_dry_run_report(
    per_source: dict[str, int],
    per_source_rejected: dict[str, int],
    reject_reasons: Counter,
    total_raw: int,
    total_normalized: int,
    total_candidates: int,
    elapsed_s: float,
    enabled_connector_names: list[str],
    disabled_connector_names: list[str],
) -> None:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    est_cost_usd = total_candidates * _AVG_COST_PER_CALL_USD

    lines = [
        "# Migration Dry-Run Report",
        "",
        f"- **Date**: {now}",
        "- **Mode**: dry-run (no DB writes)",
        f"- **Database**: {settings.database_url} / {settings.mongo_db}",
        f"- **Elapsed**: {elapsed_s:.1f}s",
        "",
        "## Connectors",
        "",
        f"- Enabled: {len(enabled_connector_names)} → {', '.join(enabled_connector_names) or '—'}",
        f"- Disabled: {len(disabled_connector_names)} → {', '.join(disabled_connector_names) or '—'}",
        "",
        "## Totals",
        "",
        "| Stage | Count |",
        "|-------|------:|",
        f"| Raw fetched | {total_raw:,} |",
        f"| Normalized | {total_normalized:,} |",
        f"| Pre-filter rejected | {total_normalized - total_candidates + (total_raw - total_normalized):,} |",
        f"| Pre-filter passed (AI candidates) | {total_candidates:,} |",
        "",
        "## Per Source",
        "",
        "| Source | Would Import | Rejected |",
        "|--------|------------:|---------:|",
    ]

    all_sources = sorted(set(per_source) | set(per_source_rejected))
    for src in all_sources:
        lines.append(
            f"| {src} | {per_source.get(src, 0):,} | {per_source_rejected.get(src, 0):,} |"
        )

    lines += [
        "",
        "## Reject Reasons",
        "",
        "| Reason | Count |",
        "|--------|------:|",
    ]
    for reason, count in reject_reasons.most_common():
        lines.append(f"| {reason} | {count:,} |")
    if not reject_reasons:
        lines.append("| (none) | 0 |")

    lines += [
        "",
        "## Estimated Groq Cost",
        "",
        f"- AI candidates: {total_candidates:,}",
        f"- Avg cost per call (baseline): ${_AVG_COST_PER_CALL_USD:.6f}",
        f"- **Estimated total**: ${est_cost_usd:.4f}",
        "",
        "Notes: cost is an upper bound — dedupe hits skip the AI call.",
        "Salary, seniority, and quality-gate stages run only on candidates.",
        "",
        "## Next Steps",
        "",
        "1. Review counts per source. Investigate any unexpected zeros.",
        "2. Review top reject reasons. Tune pre-filter if a source is",
        "   over-rejected.",
        "3. Confirm estimated cost is acceptable.",
        "4. Follow `docs/runbooks/migration.md` to execute with `--confirm`.",
        "",
    ]

    REPORT_PATH.write_text("\n".join(lines))
    log.info("migrate.report_written", path=str(REPORT_PATH))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/migrate.py",
        description="Wipe-and-reimport migration. Default = dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run (default behavior when --confirm is absent).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="REQUIRED for real execution. Drops the jobs collection.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dry_run = (not args.confirm) or args.dry_run

    enabled_names = [
        name for name, entry in REGISTRY.items() if entry.enabled
    ]
    disabled_names = [
        name for name, entry in REGISTRY.items() if not entry.enabled
    ]

    log.info(
        "migrate.start",
        dry_run=dry_run,
        db=settings.mongo_db,
        connectors_enabled=len(enabled_names),
    )

    if not dry_run:
        log.warning(
            "migrate.destructive_mode",
            message="--confirm passed: jobs collection WILL be dropped",
        )

    total_steps = 5
    t0 = time.monotonic()

    # Step 1 — preflight
    _step(1, total_steps, "preflight checks")
    jobs_col = get_jobs()
    pre_count = jobs_col.count_documents({})
    log.info("migrate.preflight", existing_jobs=pre_count)

    # Step 2 — fetch + normalize + prefilter (always; cost-estimation source)
    _step(2, total_steps, "fetch + normalize + prefilter (no writes)")
    connectors = get_enabled_connectors()
    (
        per_source,
        per_source_rejected,
        reject_reasons,
        total_raw,
        total_normalized,
        total_candidates,
    ) = _fetch_and_prefilter(connectors)
    log.info(
        "migrate.prefilter_summary",
        total_raw=total_raw,
        total_normalized=total_normalized,
        total_candidates=total_candidates,
    )

    if dry_run:
        # Step 3 (dry-run): write report and exit. No DB writes.
        _step(3, total_steps, "write dry-run report")
        elapsed = time.monotonic() - t0
        _write_dry_run_report(
            per_source=per_source,
            per_source_rejected=per_source_rejected,
            reject_reasons=reject_reasons,
            total_raw=total_raw,
            total_normalized=total_normalized,
            total_candidates=total_candidates,
            elapsed_s=elapsed,
            enabled_connector_names=enabled_names,
            disabled_connector_names=disabled_names,
        )
        log.info(
            "migrate.dry_run_complete",
            elapsed_s=round(elapsed, 1),
            ai_candidates=total_candidates,
            est_cost_usd=round(total_candidates * _AVG_COST_PER_CALL_USD, 4),
            report=str(REPORT_PATH),
        )
        return 0

    # --confirm path below — DESTRUCTIVE.

    # Step 3 — drop jobs collection (idempotent: skip if empty)
    _step(3, total_steps, "drop jobs collection")
    if pre_count == 0:
        log.info("migrate.drop_skipped", reason="collection already empty")
    else:
        jobs_col.drop()
        log.info("migrate.dropped", deleted_docs=pre_count)

    # Step 4 — recreate indexes
    _step(4, total_steps, "recreate indexes")
    ensure_indexes()

    # Step 5 — full re-import via pipeline
    _step(5, total_steps, "full re-import via pipeline")
    from import_service.cli import cmd_import  # late import: avoid heavy deps

    rc = cmd_import(argparse.Namespace(dry_run=False, limit=0))
    elapsed = time.monotonic() - t0
    final_count = jobs_col.count_documents({})
    log.info(
        "migrate.complete",
        elapsed_s=round(elapsed, 1),
        final_jobs=final_count,
        rc=rc,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
