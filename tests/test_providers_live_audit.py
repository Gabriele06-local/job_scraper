"""Live provider audit — runs real HTTP calls against each connector.

Skip in CI. Run manually:
    LIVE=1 pytest tests/test_providers_live_audit.py -v -s

Auto-elimination thresholds (logged, not enforced as failures):
  - Zero jobs returned → ERROR
  - Pass rate through prefilter < 30% → WARNING
  - Response time > 10s → WARNING
"""

from __future__ import annotations

import itertools
import os
import time
from typing import Any

import pytest

from connectors import REGISTRY

pytestmark = pytest.mark.skipif(
    not os.getenv("LIVE"),
    reason="Set LIVE=1 to run live provider audit",
)

_SAMPLE_SIZE = 20
_RESPONSE_TIME_WARN = 10.0
_PASS_RATE_WARN = 0.30


def _audit_connector(name: str, connector: Any) -> dict:
    start = time.monotonic()
    jobs: list[dict] = []
    try:
        for job in itertools.islice(connector.fetch(), _SAMPLE_SIZE):
            jobs.append(job)
    except Exception as exc:
        return {
            "name": name,
            "status": "ERROR",
            "jobs": 0,
            "pass_rate": 0.0,
            "elapsed": time.monotonic() - start,
            "error": str(exc),
        }
    elapsed = time.monotonic() - start

    passed = 0
    for job in jobs:
        # Minimal quality bar: has title, url/link, and non-trivial content.
        title = job.get("title") or ""
        url = job.get("url") or job.get("link") or job.get("applicationUrl") or ""
        desc = job.get("description") or job.get("content") or ""
        if title and url and len(desc) >= 50:
            passed += 1

    pass_rate = passed / len(jobs) if jobs else 0.0
    return {
        "name": name,
        "status": "OK",
        "jobs": len(jobs),
        "pass_rate": pass_rate,
        "elapsed": elapsed,
        "error": None,
    }


def _print_table(results: list[dict]) -> None:
    print("\n")
    print(f"{'Provider':<20} {'Jobs':>6} {'Pass%':>7} {'Time(s)':>8}  Status")
    print("-" * 60)
    for r in results:
        pct = f"{r['pass_rate']*100:.0f}%" if r["jobs"] else "N/A"
        flag = ""
        if r["status"] == "ERROR":
            flag = "  [ERROR]"
        elif r["jobs"] == 0:
            flag = "  [NO JOBS]"
        elif r["pass_rate"] < _PASS_RATE_WARN:
            flag = "  [LOW PASS RATE]"
        elif r["elapsed"] > _RESPONSE_TIME_WARN:
            flag = "  [SLOW]"
        print(
            f"{r['name']:<20} {r['jobs']:>6} {pct:>7} {r['elapsed']:>8.1f}{flag}"
        )
    print()


@pytest.mark.parametrize("name", list(REGISTRY.keys()))
def test_provider_live(name: str) -> None:
    if not REGISTRY[name].enabled:
        pytest.skip(f"{name} disabled in registry")

    try:
        connector = REGISTRY[name].cls()
    except Exception as exc:
        pytest.skip(f"{name}: init failed — {exc}")

    result = _audit_connector(name, connector)
    _print_table([result])

    if result["status"] == "ERROR":
        pytest.fail(f"{name}: connector errored — {result['error']}")

    if result["jobs"] == 0:
        pytest.fail(f"{name}: returned zero jobs")

    if result["pass_rate"] < _PASS_RATE_WARN:
        pytest.xfail(
            f"{name}: pass rate {result['pass_rate']:.0%} < {_PASS_RATE_WARN:.0%}"
        )

    if result["elapsed"] > _RESPONSE_TIME_WARN:
        pytest.xfail(f"{name}: response time {result['elapsed']:.1f}s > {_RESPONSE_TIME_WARN}s")
