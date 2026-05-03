"""Import pipeline orchestrator — SPEC 00.

Stages: Pre-filter → Dedupe → AI Classify → Quality Gate → Persist.
Fetch + Normalize are handled by scrapers upstream; this class starts from RawJob.

Idempotent: dedup_hash prevents double-processing. dry_run skips Mongo writes.
"""

from __future__ import annotations

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
    RawJob,
    compute_dedup_hash,
    normalize_text,
)
from pipeline.dedupe import check_fuzzy_dup, find_existing_job, merge_with_existing
from pipeline.language_detector import detect_language
from pipeline.prefilter import should_send_to_ai
from pipeline.quality_gate import QualityRejectReason, evaluate

logger = structlog.get_logger(__name__)


@dataclass
class PipelineCounters:
    """Per-run stage counters."""

    total: int = 0
    prefilter_rejected: int = 0
    dedupe_hit: int = 0
    fuzzy_dup_flagged: int = 0
    ai_classified: int = 0
    ai_unavailable: int = 0
    gate_valid: int = 0
    gate_premium: int = 0
    gate_rejected: int = 0
    persisted: int = 0
    dry_run_skipped: int = 0


@dataclass
class PipelineResult:
    """Result of a single pipeline run."""

    counters: PipelineCounters = field(default_factory=PipelineCounters)
    errors: list[str] = field(default_factory=list)
    cost_summary: dict = field(default_factory=dict)  # type: ignore[type-arg]


class ImportPipeline:
    """Stateless import pipeline: Pre-filter → Dedupe → AI → Gate → Persist.

    Args:
        jobs_col: MongoDB jobs collection (real or mongomock for tests).
        classifier: GroqClassifier instance (injected for test mocking).
        dry_run: If True, skip all Mongo writes.
    """

    def __init__(
        self,
        jobs_col: Collection,  # type: ignore[type-arg]
        classifier: Optional[GroqClassifier] = None,
        dry_run: bool = False,
    ) -> None:
        self._jobs_col = jobs_col
        self._classifier = classifier or GroqClassifier()
        self._dry_run = dry_run

    def run(self, raw_jobs: list[RawJob]) -> PipelineResult:
        """Process a batch of normalized RawJobs through all pipeline stages."""
        result = PipelineResult()
        c = result.counters
        c.total = len(raw_jobs)

        for raw in raw_jobs:
            try:
                self._process_one(raw, c)
            except Exception as exc:
                logger.error("pipeline.unexpected_error", url=raw.url, error=str(exc))
                result.errors.append(f"{raw.url}: {exc}")

        result.cost_summary = cost_tracker.summary()

        logger.info(
            "pipeline.run_complete",
            total=c.total,
            prefilter_rejected=c.prefilter_rejected,
            dedupe_hit=c.dedupe_hit,
            fuzzy_dup_flagged=c.fuzzy_dup_flagged,
            ai_classified=c.ai_classified,
            ai_unavailable=c.ai_unavailable,
            gate_valid=c.gate_valid,
            gate_premium=c.gate_premium,
            gate_rejected=c.gate_rejected,
            persisted=c.persisted,
            dry_run=self._dry_run,
            **result.cost_summary,
        )
        return result

    # ------------------------------------------------------------------
    # Private stage methods
    # ------------------------------------------------------------------

    def _process_one(self, raw: RawJob, c: PipelineCounters) -> None:
        # Stage 1: Pre-filter
        passes, reason = should_send_to_ai(raw)
        if not passes:
            c.prefilter_rejected += 1
            if not self._dry_run:
                self._persist_rejected_prefilter(raw, reason)
            return

        # Stage 2: Hash dedupe
        dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
        existing = find_existing_job(dedup_hash, self._jobs_col)
        if existing is not None:
            c.dedupe_hit += 1
            if not self._dry_run:
                merge_with_existing(existing, raw, self._jobs_col)
            return

        # Stage 2b: Fuzzy dedupe (flag only — don't skip, just count)
        if check_fuzzy_dup(raw, self._jobs_col):
            c.fuzzy_dup_flagged += 1

        # Stage 3: AI classification
        job = self._raw_to_job(raw, dedup_hash)
        classification = self._classifier.classify(self._to_classify_input(raw))

        if classification is None:
            c.ai_unavailable += 1
            job.status = JobStatus.REJECTED_QUALITY
            job.reject_reason = QualityRejectReason.AI_UNAVAILABLE.value
            job.quality = JobQuality(quality_score=0)
        else:
            c.ai_classified += 1
            job.classification = classification
            # Sync salary from classification to top-level JobSalary
            job.salary = JobSalary(
                min=classification.salary_min,
                max=classification.salary_max,
                currency=classification.currency,
            )

            # Stage 4: Quality gate
            job = evaluate(job)
            if job.status == JobStatus.PREMIUM:
                c.gate_premium += 1
            elif job.status == JobStatus.VALID:
                c.gate_valid += 1
            else:
                c.gate_rejected += 1

        # Stage 5: Persist
        if self._dry_run:
            c.dry_run_skipped += 1
        else:
            self._persist(job)
            c.persisted += 1

    def _to_classify_input(self, raw: RawJob) -> dict:  # type: ignore[type-arg]
        """Build the dict expected by GroqClassifier.classify()."""
        return {
            "title": raw.title,
            "company_name": raw.company_name,
            "location_raw": raw.location_raw or "unknown",
            "detected_language": raw.original_language or "unknown",
            "description": raw.description,
            "url": raw.url,
        }

    def _raw_to_job(self, raw: RawJob, dedup_hash: str) -> Job:
        """Construct a bare Job from RawJob before AI classification."""
        now = datetime.now(tz=timezone.utc)
        detected_lang, _ = detect_language(raw.description[:2000])

        return Job(
            url=raw.url,
            dedup_hash=dedup_hash,
            source_info=JobSource(
                source=raw.source,
                external_id=raw.external_id,
            ),
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
        """Persist a prefilter-rejected job (dedup_hash prevents AI re-run next run)."""
        dedup_hash = compute_dedup_hash(raw.title, raw.company_name, raw.source)
        if self._jobs_col.find_one({"dedup_hash": dedup_hash}):
            return  # Already persisted — idempotent

        job = self._raw_to_job(raw, dedup_hash)
        job.status = JobStatus.REJECTED_PREFILTER
        job.reject_reason = reason

        try:
            self._jobs_col.insert_one(job.to_mongo_doc())
        except pymongo.errors.DuplicateKeyError:
            pass  # Race: another process beat us — safe to ignore

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
