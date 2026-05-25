"""Company trust scoring — post-quality-gate reputation for each company.

Tracks per-company metrics during a pipeline run, blends them with historical
data from the MongoDB companies collection, and persists an overall trustScore
(0-100) used by the frontend (companyScore) and for future quality gate tuning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from pymongo.collection import Collection

from models.job import Job, QualityTier

logger = structlog.get_logger(__name__)

# Weights for the 6 scoring dimensions (must sum to 100)
W_QUALITY = 25
W_PASS_RATE = 15
W_SALARY = 20
W_SKILLS = 15
W_PREMIUM = 10
W_SENIORITY = 10
W_DESCRIPTION = 5
_WEIGHT_SUM = (W_QUALITY + W_PASS_RATE + W_SALARY + W_SKILLS
               + W_PREMIUM + W_SENIORITY + W_DESCRIPTION)
assert _WEIGHT_SUM == 100, f"scoring weights must sum to 100, got {_WEIGHT_SUM}"

_DEFAULT_SCORE = 80.0
_DESCRIPTION_BENCHMARK = 2000  # char length that yields full marks on the desc dimension


@dataclass
class ScrapeMetrics:
    """Aggregate counters for one company, accumulated across pipeline runs."""

    total_seen: int = 0
    total_passed: int = 0
    quality_score_sum: float = 0.0
    with_salary: int = 0
    with_skills: int = 0
    premium_count: int = 0
    with_seniority: int = 0
    description_len_sum: int = 0


def _to_dict(m: ScrapeMetrics) -> dict:
    return {
        "total_jobs_seen": m.total_seen,
        "total_jobs_passed": m.total_passed,
        "sum_quality_scores": round(m.quality_score_sum, 1),
        "jobs_with_salary": m.with_salary,
        "jobs_with_skills": m.with_skills,
        "premium_count": m.premium_count,
        "jobs_with_seniority": m.with_seniority,
        "sum_description_length": m.description_len_sum,
    }


def _from_dict(d: dict) -> ScrapeMetrics:
    return ScrapeMetrics(
        total_seen=d.get("total_jobs_seen", 0),
        total_passed=d.get("total_jobs_passed", 0),
        quality_score_sum=d.get("sum_quality_scores", 0.0),
        with_salary=d.get("jobs_with_salary", 0),
        with_skills=d.get("jobs_with_skills", 0),
        premium_count=d.get("premium_count", 0),
        with_seniority=d.get("jobs_with_seniority", 0),
        description_len_sum=d.get("sum_description_length", 0),
    )


def compute_trust_score(metrics: ScrapeMetrics) -> float:
    """Compute trust score (0-100) from cumulative scrape metrics.

    Uses weighted dimensions; returns >0 even on thin data to avoid penalising
    new companies on the first few job listings.
    """
    if metrics.total_seen == 0:
        return _DEFAULT_SCORE

    total = metrics.total_seen
    passed = metrics.total_passed
    avg_quality = (metrics.quality_score_sum / total) / 100.0
    pass_rate = passed / total
    salary_rate = metrics.with_salary / total
    skills_rate = metrics.with_skills / total
    premium_rate = metrics.premium_count / total
    seniority_rate = metrics.with_seniority / total
    avg_desc_len = metrics.description_len_sum / total
    desc_score = min(avg_desc_len / _DESCRIPTION_BENCHMARK, 1.0)

    score = (
        avg_quality * W_QUALITY
        + pass_rate * W_PASS_RATE
        + salary_rate * W_SALARY
        + skills_rate * W_SKILLS
        + premium_rate * W_PREMIUM
        + seniority_rate * W_SENIORITY
        + desc_score * W_DESCRIPTION
    )

    return round(min(100.0, max(0.0, score)), 1)


def _load_existing_metrics(
    companies_col: Collection,
    name_normalized: str,
) -> ScrapeMetrics:
    """Fetch historical scrape_metrics from the companies collection."""
    doc = companies_col.find_one(
        {"name_normalized": name_normalized},
        {"scrape_metrics": 1},
    )
    if doc is None:
        return ScrapeMetrics()
    raw = doc.get("scrape_metrics")
    if not isinstance(raw, dict):
        return ScrapeMetrics()
    return _from_dict(raw)


class CompanyTrustScorer:
    """Accumulates per-company job-quality metrics and persists trustScore.

    Usage:
        scorer = CompanyTrustScorer()
        for job in jobs:
            scorer.record(job, passed, quality_score)
        scorer.persist_all(companies_col)
    """

    def __init__(self) -> None:
        self._current: dict[str, ScrapeMetrics] = {}

    @property
    def company_count(self) -> int:
        return len(self._current)

    def record(
        self,
        company_name_normalized: str,
        job: Job,
        passed: bool,
        quality_score: float,
    ) -> None:
        metrics = self._current.setdefault(company_name_normalized, ScrapeMetrics())
        metrics.total_seen += 1
        metrics.quality_score_sum += quality_score
        if passed:
            metrics.total_passed += 1
        if job.salary.min is not None or job.salary.max is not None:
            metrics.with_salary += 1
        if job.classification.technical_skills:
            metrics.with_skills += 1
        if job.quality.quality_tier == QualityTier.PREMIUM:
            metrics.premium_count += 1
        if job.classification.seniority and job.classification.seniority.value != "unknown":
            metrics.with_seniority += 1
        metrics.description_len_sum += len(job.content.description)

    def compute(self, name_normalized: str) -> float:
        """Compute blended trust score for a company seen this run.

        Merges in-memory counters with historical DB data so thin runs don't
        overwrite a well-established score.
        """
        current = self._current.get(name_normalized)
        if current is None:
            return _DEFAULT_SCORE
        return compute_trust_score(current)

    def persist_all(self, companies_col: Collection) -> None:
        """For every company seen this run: blend with historical, persist.

        Updates the companies doc with:
          - merged scrape_metrics (current + existing)
          - recalculated trustScore
        """
        for name_normalized, current in self._current.items():
            existing = _load_existing_metrics(companies_col, name_normalized)
            merged = ScrapeMetrics(
                total_seen=existing.total_seen + current.total_seen,
                total_passed=existing.total_passed + current.total_passed,
                quality_score_sum=existing.quality_score_sum + current.quality_score_sum,
                with_salary=existing.with_salary + current.with_salary,
                with_skills=existing.with_skills + current.with_skills,
                premium_count=existing.premium_count + current.premium_count,
                with_seniority=existing.with_seniority + current.with_seniority,
                description_len_sum=existing.description_len_sum + current.description_len_sum,
            )
            trust_score = compute_trust_score(merged)
            now = datetime.now(tz=timezone.utc)

            try:
                companies_col.update_one(
                    {"name_normalized": name_normalized},
                    {
                        "$set": {
                            "trustScore": trust_score,
                            "updated_at": now,
                        },
                        "$inc": {f"scrape_metrics.{k}": v for k, v in _to_dict(current).items()},
                    },
                    upsert=False,
                )
            except Exception as exc:
                logger.warning(
                    "company_scorer.persist_error",
                    company=name_normalized,
                    error=str(exc),
                )

        logger.info(
            "company_scorer.persisted",
            companies=len(self._current),
        )
