"""Import pipeline orchestrator — SPEC 00 + SDD strict.

Stages: Pre-filter → URL validity (Stage 0.5) → Dedupe → AI Classify →
        Quality Gate → Persist.

Fetch + Normalize are handled by scrapers upstream; this class starts from RawJob.

Idempotent: dedup_hash prevents double-processing. `dry_run=True` skips Mongo writes.

Pipeline emits per-run aggregates into `PipelineResult.counters` plus, when a
`ImportReportTracker` is injected, controlled-vocabulary failure counts via
`report_tracker.add_failure(...)` (SDD §I.3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pymongo
import structlog
from pymongo.collection import Collection

from ai.classifier import GroqClassifier, cost_tracker
from models.job import (
    Job,
    JobCompany,
    JobContent,
    JobLocation,
    JobQuality,
    JobSalary,
    JobSource,
    JobStatus,
    QualityTier,
    RawJob,
    compute_cross_source_hash,
    compute_dedup_hash,
    normalize_text,
)
from pipeline.company_scorer import CompanyTrustScorer
from pipeline.dedupe import (
    check_fuzzy_dup,
    find_cross_source_dup,
    find_existing_job,
    merge_cross_source,
    merge_with_existing,
)
from pipeline.language_detector import detect_language
from pipeline.prefilter import should_send_to_ai
from pipeline.quality_gate import QualityRejectReason, evaluate
from pipeline.report import ImportReportTracker
from pipeline.url_validator import URLValidationResult

logger = structlog.get_logger(__name__)


@dataclass
class PipelineCounters:
    """Per-run stage counters."""

    total: int = 0
    prefilter_rejected: int = 0
    url_invalid: int = 0
    dedupe_hit: int = 0
    cross_source_hit: int = 0
    fuzzy_dup_flagged: int = 0
    ai_classified: int = 0
    ai_unavailable: int = 0
    gate_valid: int = 0
    gate_premium: int = 0
    gate_rejected: int = 0
    persisted: int = 0
    dry_run_skipped: int = 0
    # SDD additions
    geocode_pending: int = 0
    enrichment_ms_sum: float = 0.0
    enrichment_ms_count: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)

    def average_enrichment_ms(self) -> float:
        if self.enrichment_ms_count == 0:
            return 0.0
        return self.enrichment_ms_sum / self.enrichment_ms_count


@dataclass
class PipelineResult:
    """Result of a single pipeline run."""

    counters: PipelineCounters = field(default_factory=PipelineCounters)
    errors: list[str] = field(default_factory=list)
    cost_summary: dict = field(default_factory=dict)  # type: ignore[type-arg]


class ImportPipeline:
    """Stateless import pipeline: Pre-filter → URL → Dedupe → AI → Gate → Persist.

    Args:
        jobs_col: MongoDB jobs collection (real or mongomock).
        companies_col: MongoDB companies collection for upsert/linking.
        classifier: GroqClassifier (injected for tests).
        dry_run: When True, skip all Mongo writes.
        report_tracker: Optional ImportReportTracker — when present, the
            orchestrator emits `add_failure(...)` calls for each rejection
            and `record_enrichment_ms(...)` for each Groq call.
        report_id: run_id returned by `report_tracker.start(...)`; required
            when `report_tracker` is supplied.
    """

    def __init__(
        self,
        jobs_col: Collection,  # type: ignore[type-arg]
        companies_col: Collection,  # type: ignore[type-arg]
        classifier: Optional[GroqClassifier] = None,
        dry_run: bool = False,
        report_tracker: ImportReportTracker | None = None,
        report_id: str | None = None,
    ) -> None:
        self._jobs_col = jobs_col
        self._companies_col = companies_col
        self._classifier = classifier or GroqClassifier()
        self._dry_run = dry_run
        self._report_tracker = report_tracker
        self._report_id = report_id
        self._company_scorer = CompanyTrustScorer()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        raw_jobs: list[RawJob],
        *,
        url_results: dict[str, URLValidationResult] | None = None,
    ) -> PipelineResult:
        """Process a batch of normalized RawJobs through all pipeline stages.

        Args:
            raw_jobs: Output of upstream Normalize stage.
            url_results: Optional pre-pipeline URL probe results
                (`PrePipelineURLValidator.validate_many`). Jobs whose URL is in
                this dict with `is_valid=False` are persisted as `EXPIRED`
                without invoking the AI classifier.
        """
        result = PipelineResult()
        c = result.counters
        c.total = len(raw_jobs)

        for raw in raw_jobs:
            try:
                self._process_one(raw, c, result.errors, url_results or {})
            except Exception as exc:
                logger.error("pipeline.unexpected_error", url=raw.url, error=str(exc))
                result.errors.append(f"{raw.url}: {exc}")

        result.cost_summary = cost_tracker.summary()

        # Persist company trust scores
        if not self._dry_run and self._company_scorer.company_count > 0:
            self._company_scorer.persist_all(self._companies_col)

        logger.info(
            "pipeline.run_complete",
            total=c.total,
            prefilter_rejected=c.prefilter_rejected,
            url_invalid=c.url_invalid,
            dedupe_hit=c.dedupe_hit,
            fuzzy_dup_flagged=c.fuzzy_dup_flagged,
            ai_classified=c.ai_classified,
            ai_unavailable=c.ai_unavailable,
            gate_valid=c.gate_valid,
            gate_premium=c.gate_premium,
            gate_rejected=c.gate_rejected,
            persisted=c.persisted,
            geocode_pending=c.geocode_pending,
            avg_enrichment_ms=round(c.average_enrichment_ms(), 1),
            dry_run=self._dry_run,
            **result.cost_summary,
        )
        return result

    # ------------------------------------------------------------------
    # Private stage methods
    # ------------------------------------------------------------------

    def _process_one(
        self,
        raw: RawJob,
        c: PipelineCounters,
        errors: list[str],
        url_results: dict[str, URLValidationResult],
    ) -> None:
        logger.debug("job.process", title=raw.title[:80], source=raw.source, url=raw.url)

        # Stage 1: Pre-filter
        passes, reason = should_send_to_ai(raw)
        if not passes:
            c.prefilter_rejected += 1
            self._tally_failure(c, _prefilter_reason_code(reason))
            logger.debug("job.prefilter_rejected", title=raw.title[:80], reason=reason)
            if not self._dry_run:
                self._persist_rejected_prefilter(raw, reason)
            return

        # Stage 0.5: URL validity (SDD §A.1 — only when we have a probe result)
        url_result = url_results.get(raw.url)
        if url_result is not None and not url_result.is_valid:
            c.url_invalid += 1
            self._tally_failure(c, QualityRejectReason.URL_INVALID.value)
            reject_code = f"URL_INVALID:{url_result.reason or 'unknown'}"
            logger.info(
                "job.url_invalid",
                title=raw.title[:80],
                url=raw.url,
                status_code=url_result.status_code,
                reason=url_result.reason,
            )
            if not self._dry_run:
                self._persist_url_invalid(raw, reject_code)
            return

        # Stage 2: Hash dedupe
        dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
        existing = find_existing_job(dedup_hash, self._jobs_col)
        if existing is not None:
            c.dedupe_hit += 1
            self._tally_failure(c, QualityRejectReason.DUPLICATE.value)
            logger.debug("job.dedupe_hit", title=raw.title[:80])
            if not self._dry_run:
                merge_with_existing(existing, raw, self._jobs_col)
            return

        # Stage 2a: Cross-source dedup
        cross_source_hash = compute_cross_source_hash(raw.title, raw.company_name)
        cross_existing = find_cross_source_dup(cross_source_hash, raw.source, self._jobs_col)
        if cross_existing is not None:
            c.cross_source_hit += 1
            logger.info(
                "job.cross_source_hit",
                title=raw.title[:80],
                existing_url=cross_existing.url,
                new_url=raw.url,
                existing_source=cross_existing.source_info.source,
                new_source=raw.source,
            )
            if not self._dry_run:
                merge_cross_source(cross_existing, raw, self._jobs_col)
            return

        # Stage 2b: Fuzzy dedupe (merge)
        fuzzy_existing = check_fuzzy_dup(raw, self._jobs_col)
        if fuzzy_existing is not None:
            c.fuzzy_dup_flagged += 1
            logger.info(
                "job.fuzzy_dedup_merged",
                title=raw.title[:80],
                existing_source=fuzzy_existing.source_info.source,
                new_source=raw.source,
            )
            if not self._dry_run:
                merge_cross_source(fuzzy_existing, raw, self._jobs_col)
            return

        # Stage 3: AI classification (with per-job timing)
        job = self._raw_to_job(raw, dedup_hash)
        logger.info(
            "job.ai_classify",
            title=raw.title[:80],
            source=raw.source,
            desc_len=len(raw.description),
        )
        t0 = time.perf_counter()
        classification = self._classifier.classify(self._to_classify_input(raw))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        c.enrichment_ms_sum += elapsed_ms
        c.enrichment_ms_count += 1
        if self._report_tracker and self._report_id:
            self._report_tracker.record_enrichment_ms(self._report_id, elapsed_ms)

        if classification is None:
            c.ai_unavailable += 1
            self._tally_failure(c, QualityRejectReason.AI_CLASSIFICATION_FAILED.value)
            job.status = JobStatus.REJECTED_QUALITY
            job.reject_reason = QualityRejectReason.AI_CLASSIFICATION_FAILED.value
            job.quality = JobQuality(quality_score=0)
            logger.warning("job.ai_unavailable", title=raw.title[:80], url=raw.url)
        else:
            c.ai_classified += 1
            job.classification = classification
            logger.info(
                "job.ai_result",
                title=raw.title[:80],
                category=str(classification.category),
                seniority=str(classification.seniority),
                role_family=str(classification.role_family),
                remote_mode=str(classification.remote_mode),
                confidence=round(classification.ai_confidence, 2),
                skills=classification.technical_skills[:5],
            )
            job.salary = JobSalary(
                min=classification.salary_min,
                max=classification.salary_max,
                currency=classification.currency,
            )

            # Stage 4: Quality gate
            job = evaluate(job)
            if job.status == JobStatus.ACTIVE:
                if job.quality.quality_tier == QualityTier.PREMIUM:
                    c.gate_premium += 1
                    logger.info("job.gate_pass", title=raw.title[:80], tier="premium")
                else:
                    c.gate_valid += 1
                    logger.info("job.gate_pass", title=raw.title[:80], tier="valid")
                if job.quality.geocode_pending:
                    c.geocode_pending += 1
            else:
                c.gate_rejected += 1
                self._tally_failure(c, job.reject_reason or "UNKNOWN")
                logger.info(
                    "job.gate_rejected",
                    title=raw.title[:80],
                    reason=job.reject_reason,
                )

            # Stage 4.5: Company trust scoring
            self._company_scorer.record(
                company_name_normalized=job.company.name_normalized,
                job=job,
                passed=job.status == JobStatus.ACTIVE,
                quality_score=job.quality.quality_score,
            )

        # Stage 5: Company upsert
        if not self._dry_run:
            company_id = self._upsert_company(job)
            if company_id:
                job.company.id = company_id
                logger.debug(
                    "job.company_linked",
                    company=job.company.name,
                    company_id=company_id,
                )

        # Stage 6: Persist
        if self._dry_run:
            c.dry_run_skipped += 1
            logger.debug("job.dry_run_skip", title=raw.title[:80])
        else:
            self._persist(job)
            c.persisted += 1
            logger.debug("job.persisted", title=raw.title[:80], status=str(job.status))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tally_failure(self, c: PipelineCounters, reason: str) -> None:
        """Increment per-run + report-tracker failure-reason buckets."""
        c.failure_reasons[reason] = c.failure_reasons.get(reason, 0) + 1
        if self._report_tracker and self._report_id:
            self._report_tracker.add_failure(self._report_id, reason)

    def _to_classify_input(self, raw: RawJob) -> dict:  # type: ignore[type-arg]
        return {
            "title": raw.title,
            "company_name": raw.company_name,
            "location_raw": raw.location_raw or "unknown",
            "detected_language": raw.original_language or "unknown",
            "description": raw.description,
            "url": raw.url,
        }

    def _raw_to_job(self, raw: RawJob, dedup_hash: str) -> Job:
        now = datetime.now(tz=timezone.utc)
        detected_lang, _ = detect_language(raw.description[:2000])

        return Job(
            url=raw.url,
            dedup_hash=dedup_hash,
            cross_source_hash=compute_cross_source_hash(raw.title, raw.company_name),
            source_info=JobSource(source=raw.source, external_id=raw.external_id),
            content=JobContent(
                title=raw.title,
                title_normalized=normalize_text(raw.title),
                description=raw.description,
                language=detected_lang,
            ),
            company=JobCompany(
                name=raw.company_name,
                name_normalized=normalize_text(raw.company_name),
            ),
            location=JobLocation(raw=raw.location_raw),
            salary=JobSalary(
                min=raw.salary_min,
                max=raw.salary_max,
                currency=raw.currency,
            ),
            posted_at=raw.posted_at or now,
            first_seen_at=now,
            last_seen_at=now,
        )

    def _persist_rejected_prefilter(self, raw: RawJob, reason: str) -> None:
        """Persist a prefilter-rejected job (idempotent via dedup_hash)."""
        dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
        if self._jobs_col.find_one({"dedup_hash": dedup_hash}):
            return

        job = self._raw_to_job(raw, dedup_hash)
        job.status = JobStatus.REJECTED_PREFILTER
        job.reject_reason = reason

        try:
            self._jobs_col.insert_one(job.to_mongo_doc())
        except pymongo.errors.DuplicateKeyError:
            pass

    def _persist_url_invalid(self, raw: RawJob, reject_code: str) -> None:
        """Persist a dead-URL stub as EXPIRED (SDD §A.1 / §I.4)."""
        dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
        if self._jobs_col.find_one({"dedup_hash": dedup_hash}):
            return

        job = self._raw_to_job(raw, dedup_hash)
        job.status = JobStatus.EXPIRED
        job.reject_reason = reject_code
        job.expires_at = datetime.now(tz=timezone.utc)
        try:
            self._jobs_col.insert_one(job.to_mongo_doc())
        except pymongo.errors.DuplicateKeyError:
            pass

    def _upsert_company(self, job: Job) -> str | None:
        """Upsert company by name_normalized; returns str(_id)."""
        from pymongo import ReturnDocument

        now = datetime.now(tz=timezone.utc)
        try:
            result = self._companies_col.find_one_and_update(
                {"name_normalized": job.company.name_normalized},
                {
                    "$setOnInsert": {
                        "name": job.company.name,
                        "name_normalized": job.company.name_normalized,
                        "trustScore": 80.0,
                        "totalRatings": 0,
                        "totalLikes": 0,
                        "totalDislikes": 0,
                        "created_at": now,
                    },
                    "$set": {"updated_at": now},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
                projection={"_id": 1},
            )
            return str(result["_id"]) if result else None
        except Exception as exc:
            logger.warning(
                "pipeline.company_upsert_error",
                company=job.company.name,
                error=str(exc),
            )
            return None

    def _persist(self, job: Job) -> None:
        """Upsert job to MongoDB by dedup_hash (idempotent)."""
        try:
            self._jobs_col.replace_one(
                {"dedup_hash": job.dedup_hash},
                job.to_mongo_doc(),
                upsert=True,
            )
        except pymongo.errors.DuplicateKeyError:
            logger.warning("pipeline.duplicate_key_on_persist", url=job.url)


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _prefilter_reason_code(raw_reason: str) -> str:
    """Normalize prefilter reason → controlled-vocab key.

    Existing prefilter codes already use `PREFILTER_*`-style upper-case strings
    (DESCRIPTION_TOO_SHORT, etc.). We just prefix with PREFILTER_ so the
    backend can dispatch on the leading token.
    """
    if not raw_reason:
        return "PREFILTER_OTHER"
    if raw_reason.startswith("PREFILTER_"):
        return raw_reason
    return f"PREFILTER_{raw_reason}"
