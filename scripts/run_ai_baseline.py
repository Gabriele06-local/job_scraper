#!/usr/bin/env python3
"""Ground truth baseline — real Groq API calls.

Runs GroqClassifier.classify() on all 30 fixtures, compares to expected_output,
computes accuracy metrics, writes docs/reports/01-ai-baseline.md.

Usage:
    python scripts/run_ai_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.classifier import GroqClassifier, cost_tracker  # noqa: E402
from config import settings  # noqa: E402

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "ground_truth"
REPORT_PATH = Path(__file__).parent.parent / "docs" / "reports" / "01-ai-baseline.md"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

_SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "principal", "unknown"]


def load_fixtures() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(FIXTURES_DIR.glob("*.json"))]


def jaccard_skills(expected: list[str], actual: list[str]) -> float:
    """Intersection-over-union for skill sets (case-insensitive)."""
    exp = {s.lower() for s in expected}
    act = {s.lower() for s in actual}
    if not exp and not act:
        return 1.0
    if not exp or not act:
        return 0.0
    return len(exp & act) / len(exp | act)


def skills_precision(expected: list[str], actual: list[str]) -> float:
    if not actual:
        return 0.0
    exp = {s.lower() for s in expected}
    act = {s.lower() for s in actual}
    return len(exp & act) / len(act)


def skills_recall(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 1.0
    exp = {s.lower() for s in expected}
    act = {s.lower() for s in actual}
    return len(exp & act) / len(exp)


def run_baseline() -> None:
    fixtures = load_fixtures()
    clf = GroqClassifier()

    # Only classify offers with expected ai_output (skip prefilter rejects)
    classifiable = [f for f in fixtures if f["expected_output"].get("ai_output") is not None]
    skipped = len(fixtures) - len(classifiable)

    print(f"Fixtures total: {len(fixtures)}, classifiable: {len(classifiable)}, skipped: {skipped}")

    results: list[dict[str, Any]] = []
    t_start = time.monotonic()

    for i, f in enumerate(classifiable, 1):
        inp = f["input"]
        expected_ai = f["expected_output"]["ai_output"]
        expected_status = f["expected_output"]["expected_status"]

        job_raw = {
            "title": inp["title"],
            "company_name": inp["company_name"],
            "location_raw": inp.get("location_raw", "unknown"),
            "detected_language": inp.get("detected_language", "unknown"),
            "description": inp["description"],
            "url": inp["url"],
        }

        print(f"[{i:02d}/{len(classifiable)}] {inp['title'][:50]}...", end=" ", flush=True)
        t0 = time.monotonic()
        classification = clf.classify(job_raw)
        elapsed = round((time.monotonic() - t0) * 1000)
        print(f"{elapsed}ms")

        if classification is None:
            results.append(
                {
                    "fixture": Path(f.get("_fixture_name", inp["url"])).stem
                    if "_fixture_name" in f
                    else inp["url"],
                    "title": inp["title"],
                    "expected_status": expected_status,
                    "ai_failed": True,
                }
            )
            continue

        actual_skills = classification.technical_skills
        expected_skills = expected_ai.get("skills", [])

        results.append(
            {
                "fixture": f.get("_fixture_name", inp["url"]),
                "title": inp["title"],
                "expected_status": expected_status,
                "ai_failed": False,
                # Seniority
                "expected_seniority": expected_ai["seniority"],
                "actual_seniority": classification.seniority.value,
                "seniority_correct": classification.seniority.value == expected_ai["seniority"],
                # Role family
                "expected_role_family": expected_ai["role_family"],
                "actual_role_family": classification.role_family.value,
                "role_family_correct": classification.role_family.value
                == expected_ai["role_family"],
                # Skills
                "expected_skills_count": len(expected_skills),
                "actual_skills_count": len(actual_skills),
                "skills_jaccard": jaccard_skills(expected_skills, actual_skills),
                "skills_precision": skills_precision(expected_skills, actual_skills),
                "skills_recall": skills_recall(expected_skills, actual_skills),
                # Salary MAE
                "expected_salary_min": expected_ai.get("salary_min"),
                "actual_salary_min": classification.salary_min,
                "expected_salary_max": expected_ai.get("salary_max"),
                "actual_salary_max": classification.salary_max,
                # Confidence
                "expected_confidence": expected_ai["confidence"],
                "actual_confidence": classification.ai_confidence,
            }
        )

    elapsed_total = round(time.monotonic() - t_start)

    # Compute aggregate metrics
    valid_results = [r for r in results if not r.get("ai_failed")]
    n = len(valid_results)

    seniority_acc = sum(r["seniority_correct"] for r in valid_results) / n if n else 0
    role_acc = sum(r["role_family_correct"] for r in valid_results) / n if n else 0
    avg_precision = sum(r["skills_precision"] for r in valid_results) / n if n else 0
    avg_recall = sum(r["skills_recall"] for r in valid_results) / n if n else 0
    avg_jaccard = sum(r["skills_jaccard"] for r in valid_results) / n if n else 0

    salary_mae_pairs = [
        (r["expected_salary_min"], r["actual_salary_min"])
        for r in valid_results
        if r.get("expected_salary_min") and r.get("actual_salary_min")
    ] + [
        (r["expected_salary_max"], r["actual_salary_max"])
        for r in valid_results
        if r.get("expected_salary_max") and r.get("actual_salary_max")
    ]
    salary_mae = (
        sum(abs(e - a) for e, a in salary_mae_pairs) / len(salary_mae_pairs)
        if salary_mae_pairs
        else None
    )

    ai_failures = sum(1 for r in results if r.get("ai_failed"))
    cost = cost_tracker.summary()

    # Cost extrapolation
    cost_per_call = cost["groq_cost_usd"] / max(n, 1)
    cost_1k = cost_per_call * 1000
    cost_10k = cost_per_call * 10000

    # Accuracy flags per SPEC (flag if <80%)
    seniority_flag = seniority_acc < 0.80
    role_flag = role_acc < 0.80

    write_report(
        results=results,
        valid_results=valid_results,
        seniority_acc=seniority_acc,
        role_acc=role_acc,
        avg_precision=avg_precision,
        avg_recall=avg_recall,
        avg_jaccard=avg_jaccard,
        salary_mae=salary_mae,
        ai_failures=ai_failures,
        cost=cost,
        cost_per_call=cost_per_call,
        cost_1k=cost_1k,
        cost_10k=cost_10k,
        elapsed_total=elapsed_total,
        seniority_flag=seniority_flag,
        role_flag=role_flag,
        skipped=skipped,
        model=settings.groq_model,
    )

    print(
        f"\nSeniority accuracy:  {seniority_acc:.1%}" + (" ⚠ FLAG <80%" if seniority_flag else "")
    )
    print(f"Role family accuracy: {role_acc:.1%}" + (" ⚠ FLAG <80%" if role_flag else ""))
    print(f"Skills precision:    {avg_precision:.1%}")
    print(f"Skills recall:       {avg_recall:.1%}")
    print(
        f"Salary MAE:          {f'EUR {salary_mae:,.0f}' if salary_mae else 'n/a (insufficient data)'}"
    )
    print(f"Cost (30 calls):     ${cost['groq_cost_usd']:.4f}")
    print(f"Cost/1k offers:      ${cost_1k:.2f}")
    print(f"Report:              {REPORT_PATH}")

    if seniority_flag or role_flag:
        print("\n⚠  Accuracy <80% flagged — see MEMORY.md for prompt tuning note")


def write_report(
    results: list[dict[str, Any]],
    valid_results: list[dict[str, Any]],
    seniority_acc: float,
    role_acc: float,
    avg_precision: float,
    avg_recall: float,
    avg_jaccard: float,
    salary_mae: float | None,
    ai_failures: int,
    cost: dict[str, Any],
    cost_per_call: float,
    cost_1k: float,
    cost_10k: float,
    elapsed_total: int,
    seniority_flag: bool,
    role_flag: bool,
    skipped: int,
    model: str,
) -> None:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = len(valid_results)

    lines = [
        "# AI Baseline Report — Ground Truth (30 offers)",
        "",
        f"- **Date**: {now}",
        f"- **Model**: {model}",
        f"- **Offers classified**: {n} / 30 ({skipped} skipped — prefilter reject, no ai_output)",
        f"- **AI failures**: {ai_failures}",
        f"- **Elapsed**: {elapsed_total}s",
        "",
        "## Summary Metrics",
        "",
        "| Metric | Value | Flag |",
        "|--------|-------|------|",
        f"| Seniority accuracy | {seniority_acc:.1%} | {'⚠ <80%' if seniority_flag else 'OK'} |",
        f"| Role family accuracy | {role_acc:.1%} | {'⚠ <80%' if role_flag else 'OK'} |",
        f"| Skills precision (avg) | {avg_precision:.1%} | |",
        f"| Skills recall (avg) | {avg_recall:.1%} | |",
        f"| Skills Jaccard (avg) | {avg_jaccard:.1%} | |",
        f"| Salary MAE | {f'EUR {salary_mae:,.0f}' if salary_mae else 'n/a'} | |",
        "",
        "## Cost",
        "",
        f"- Groq tokens in: {cost['groq_tokens_in']:,}",
        f"- Groq tokens out: {cost['groq_tokens_out']:,}",
        f"- Total cost (30 calls): ${cost['groq_cost_usd']:.4f}",
        f"- Estimated cost/1k offers: ${cost_1k:.2f}",
        f"- Estimated cost/10k offers: ${cost_10k:.2f}",
        "",
        "## Per-Offer Results",
        "",
        "| # | Title | Seniority OK | Role OK | Skills P | Skills R | Salary MAE |",
        "|---|-------|:---:|:---:|:---:|:---:|---:|",
    ]

    for i, r in enumerate(valid_results, 1):
        title = r["title"][:35]
        sen = (
            "✓"
            if r["seniority_correct"]
            else f"✗ ({r['expected_seniority']}→{r['actual_seniority']})"
        )
        role = (
            "✓"
            if r["role_family_correct"]
            else f"✗ ({r['expected_role_family']}→{r['actual_role_family']})"
        )
        prec = f"{r['skills_precision']:.0%}"
        rec = f"{r['skills_recall']:.0%}"

        sal_e_min = r.get("expected_salary_min")
        sal_a_min = r.get("actual_salary_min")
        sal_e_max = r.get("expected_salary_max")
        sal_a_max = r.get("actual_salary_max")
        if sal_e_min and sal_a_min:
            sal = f"{abs(sal_e_min - sal_a_min):,}"
        elif sal_e_max and sal_a_max:
            sal = f"{abs(sal_e_max - sal_a_max):,}"
        else:
            sal = "—"

        lines.append(f"| {i} | {title} | {sen} | {role} | {prec} | {rec} | {sal} |")

    if ai_failures:
        lines += ["", f"**AI failures**: {ai_failures} offer(s) returned None after 3 retries."]

    if seniority_flag or role_flag:
        lines += [
            "",
            "## Action Required",
            "",
        ]
        if seniority_flag:
            lines.append(
                f"- Seniority accuracy {seniority_acc:.1%} < 80% threshold. Flag in MEMORY.md for prompt tuning."
            )
        if role_flag:
            lines.append(
                f"- Role family accuracy {role_acc:.1%} < 80% threshold. Flag in MEMORY.md for prompt tuning."
            )

    REPORT_PATH.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run_baseline()
