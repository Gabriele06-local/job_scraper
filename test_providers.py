"""Standalone provider smoke tester.

Run an individual connector outside the import pipeline. Bypasses the DB-
backed enable/disable gate (useful for testing providers that are still
disabled in the backoffice).

Usage:
    python test_providers.py <slug> [--limit N] [--max-fetch M]

Examples:
    python test_providers.py jsearch
    python test_providers.py hn_hiring --limit 10
    python test_providers.py active_jobs_db --max-fetch 200
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from itertools import islice

from connectors import REGISTRY


def _print(label: str, value: object) -> None:
    print(f"    {label}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python test_providers.py",
        description="Smoke-test a single connector by slug.",
    )
    parser.add_argument(
        "slug",
        help=f"Connector slug. Available: {', '.join(sorted(REGISTRY.keys()))}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of results to display (default: 5).",
    )
    parser.add_argument(
        "--max-fetch",
        type=int,
        default=50,
        help="Safety cap on total fetched results (default: 50).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print each result as a JSON blob instead of a summary.",
    )
    args = parser.parse_args(argv)

    if args.slug not in REGISTRY:
        print(
            f"ERROR: unknown slug '{args.slug}'. Available slugs:\n  "
            + "\n  ".join(sorted(REGISTRY.keys())),
            file=sys.stderr,
        )
        return 1

    entry = REGISTRY[args.slug]
    try:
        connector = entry.cls()
    except Exception as exc:  # noqa: BLE001 — surface init failures to the user
        print(f"ERROR: failed to instantiate {args.slug}: {exc}", file=sys.stderr)
        return 1

    print(f"\n=== {args.slug} ({connector.source_name}) ===")
    print(f"Fetching up to {args.max_fetch} results...\n")

    start = time.monotonic()
    results: list[dict] = []
    try:
        for raw in islice(connector.fetch(), args.max_fetch):
            results.append(raw)
    except Exception as exc:  # noqa: BLE001 — surface fetch failures to the user
        elapsed = time.monotonic() - start
        print(
            f"ERROR after {elapsed:.2f}s and {len(results)} result(s): {exc}",
            file=sys.stderr,
        )
        return 1
    elapsed = time.monotonic() - start

    print(f"Fetched {len(results)} result(s) in {elapsed:.2f}s.")
    if not results:
        print("\n(no results — check that RAPIDAPI_KEY is set and the host is reachable)")
        return 0

    print(f"\nFirst {min(args.limit, len(results))} result(s):")
    for i, r in enumerate(results[: args.limit], 1):
        print(f"\n[{i}]")
        if args.json:
            print(json.dumps(r, indent=2, default=str, ensure_ascii=False))
            continue
        _print("title", r.get("title"))
        _print("company", r.get("company_name"))
        _print("url", r.get("url"))
        _print("location", r.get("location_raw"))
        salary = (r.get("salary_min"), r.get("salary_max"), r.get("currency"))
        if any(salary):
            _print("salary", f"{salary[0]} - {salary[1]} {salary[2] or ''}")
        _print("posted_at", r.get("published_at"))
        _print("external_id", r.get("external_id"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
