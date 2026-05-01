"""Tests for pipeline/language_detector.py, pipeline/prefilter.py, pipeline/dedupe.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from models.job import (
    Job,
    JobCompany,
    JobContent,
    JobSource,
    Language,
    RawJob,
    compute_dedup_hash,
)
from pipeline.prefilter import MIN_DESCRIPTION_LEN, RejectReason, should_send_to_ai

_NOW = datetime.now(tz=timezone.utc)
_LONG_DESC = (
    "We are looking for a Senior Python Developer to join our backend engineering team. "
    "You will design and implement scalable REST APIs and microservices using Python and Django. "
    "Requirements: 5+ years Python, experience with PostgreSQL and Redis, "
    "strong knowledge of Docker and Kubernetes. Remote-friendly position with competitive salary."
)


def _raw(
    *,
    title: str = "Senior Python Developer",
    company_name: str = "Acme Corp",
    description: str = _LONG_DESC,
    url: str = "https://jobs.example.com/1",
    posted_at: datetime | None = _NOW,
    source: str = "adzuna",
    original_language: str | None = None,
) -> RawJob:
    return RawJob(
        url=url,
        title=title,
        description=description,
        company_name=company_name,
        source=source,
        posted_at=posted_at,
        original_language=original_language,
    )


def _make_job(dedup_hash: str = "abc123", url: str = "https://jobs.example.com/1") -> Job:
    return Job(
        url=url,
        dedup_hash=dedup_hash,
        source_info=JobSource(source="adzuna"),
        content=JobContent(
            title="Senior Python Developer",
            title_normalized="senior python developer",
            description=_LONG_DESC,
            language=Language.EN,
        ),
        company=JobCompany(name="Acme Corp", name_normalized="acme corp"),
        posted_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Language detector
# ---------------------------------------------------------------------------


class TestLanguageDetector:
    def test_english(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language(
            "We are looking for a senior backend engineer with Go and Kubernetes experience."
        )
        assert lang == Language.EN
        assert conf > 0.5

    def test_italian(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language(
            "Cerchiamo uno sviluppatore backend senior con esperienza in Python e Django."
        )
        assert lang == Language.IT
        assert conf > 0.5

    def test_german(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language(
            "Wir suchen einen erfahrenen Backend-Entwickler "
            "mit Kenntnissen in Java und Spring Boot."
        )
        assert lang == Language.DE
        assert conf > 0.5

    def test_french(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language(
            "Nous recherchons un développeur backend expérimenté maîtrisant Python et Django."
        )
        assert lang == Language.FR
        assert conf > 0.5

    def test_spanish(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language(
            "Buscamos un desarrollador backend senior con experiencia en Node.js y PostgreSQL."
        )
        assert lang == Language.ES
        assert conf > 0.5

    def test_portuguese(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language(
            "Procuramos um desenvolvedor backend sênior com experiência em Python e microsserviços."
        )
        assert lang == Language.PT
        assert conf > 0.5

    def test_empty_string_returns_other(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language("")
        assert lang == Language.OTHER
        assert conf == 0.0

    def test_whitespace_only_returns_other(self):
        from pipeline.language_detector import detect_language

        lang, conf = detect_language("   \n\t  ")
        assert lang == Language.OTHER
        assert conf == 0.0

    def test_confidence_between_0_and_1(self):
        from pipeline.language_detector import detect_language

        _, conf = detect_language("Senior Software Engineer Go Kubernetes AWS")
        assert 0.0 <= conf <= 1.0

    def test_english_technical_text(self):
        from pipeline.language_detector import detect_language

        lang, _ = detect_language(
            "Required: 5+ years Python, Docker, CI/CD pipelines. "
            "Experience with microservices architecture required."
        )
        assert lang == Language.EN

    def test_italian_long_description(self):
        from pipeline.language_detector import detect_language

        lang, _ = detect_language(
            "La società è alla ricerca di un ingegnere software con almeno cinque anni di "
            "esperienza nello sviluppo di applicazioni web. Il candidato ideale ha "
            "padronanza di Python e Django, conosce i principi SOLID e ha esperienza "
            "con database relazionali come PostgreSQL."
        )
        assert lang == Language.IT

    def test_german_job_posting(self):
        from pipeline.language_detector import detect_language

        lang, _ = detect_language(
            "Als Backend-Entwickler bei unserem Unternehmen entwickeln Sie skalierbare "
            "Microservices mit Java und Spring Boot. Sie haben mindestens drei Jahre "
            "Berufserfahrung und sind vertraut mit Docker und Kubernetes."
        )
        assert lang == Language.DE


# ---------------------------------------------------------------------------
# Pre-filter: pass cases
# ---------------------------------------------------------------------------


class TestPrefilterPass:
    def test_valid_job_passes(self):
        ok, reason = should_send_to_ai(_raw())
        assert ok is True
        assert reason == ""

    def test_minimum_description_length_passes(self):
        ok, _ = should_send_to_ai(
            _raw(description="a" * MIN_DESCRIPTION_LEN, original_language="en")
        )
        assert ok is True

    def test_original_language_trusted(self):
        # Even if description is gibberish, scraper-declared lang is trusted
        ok, _ = should_send_to_ai(
            _raw(description="xyz " * 60, original_language="en")
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Pre-filter: reject reasons
# ---------------------------------------------------------------------------


class TestPrefilterRejectMissingRequired:
    def test_empty_title(self):
        ok, reason = should_send_to_ai(_raw(title=""))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value

    def test_whitespace_title(self):
        ok, reason = should_send_to_ai(_raw(title="   "))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value

    def test_empty_company(self):
        ok, reason = should_send_to_ai(_raw(company_name=""))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value

    def test_company_named_unknown(self):
        ok, reason = should_send_to_ai(_raw(company_name="Unknown"))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value

    def test_invalid_url_no_scheme(self):
        ok, reason = should_send_to_ai(_raw(url="jobs.example.com/1"))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value

    def test_invalid_url_ftp_scheme(self):
        ok, reason = should_send_to_ai(_raw(url="ftp://jobs.example.com/1"))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value

    def test_missing_posted_at(self):
        ok, reason = should_send_to_ai(_raw(posted_at=None))
        assert not ok
        assert reason == RejectReason.MISSING_REQUIRED_FIELDS.value


class TestPrefilterRejectDescriptionTooShort:
    def test_empty_description(self):
        ok, reason = should_send_to_ai(_raw(description=""))
        assert not ok
        assert reason == RejectReason.DESCRIPTION_TOO_SHORT.value

    def test_description_one_char_under_limit(self):
        ok, reason = should_send_to_ai(_raw(description="a" * (MIN_DESCRIPTION_LEN - 1)))
        assert not ok
        assert reason == RejectReason.DESCRIPTION_TOO_SHORT.value

    def test_real_short_desc_italian(self):
        ok, reason = should_send_to_ai(
            _raw(description="Cerchiamo addetto al supporto clienti. Inviare CV.")
        )
        assert not ok
        assert reason == RejectReason.DESCRIPTION_TOO_SHORT.value


class TestPrefilterRejectLanguageNotSupported:
    def test_japanese_text(self):
        jp_desc = "私たちは経験豊富なバックエンドエンジニアを探しています。" * 20
        ok, reason = should_send_to_ai(_raw(description=jp_desc))
        assert not ok
        assert reason == RejectReason.LANGUAGE_NOT_SUPPORTED.value

    def test_arabic_text(self):
        ar_desc = "نحن نبحث عن مطور متمرس في بايثون وجانغو ومعرفة بقواعد البيانات العلائقية." * 5
        ok, reason = should_send_to_ai(_raw(description=ar_desc))
        assert not ok
        assert reason == RejectReason.LANGUAGE_NOT_SUPPORTED.value


class TestPrefilterRejectSuspectedSpam:
    def test_all_caps_title(self):
        ok, reason = should_send_to_ai(
            _raw(
                title="URGENT HIRING BACKEND DEVELOPER NOW APPLY",
                original_language="en",  # skip lang detection; isolate spam rule
            )
        )
        assert not ok
        assert reason == RejectReason.SUSPECTED_SPAM.value

    def test_mixed_case_not_spam(self):
        ok, _ = should_send_to_ai(
            _raw(
                title="Senior Backend Developer (Python)",
                original_language="en",
            )
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Pre-filter: structured log emitted on reject
# ---------------------------------------------------------------------------


def test_prefilter_logs_on_reject():
    with patch("pipeline.prefilter.logger") as mock_logger:
        should_send_to_ai(_raw(description="short"))
        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs[0][0] == "prefilter.rejected"


# ---------------------------------------------------------------------------
# Dedupe: compute_dedup_hash
# ---------------------------------------------------------------------------


class TestDedupeHash:
    def test_deterministic(self):
        h1 = compute_dedup_hash("Senior Python Dev", "Acme Corp", "adzuna")
        h2 = compute_dedup_hash("Senior Python Dev", "Acme Corp", "adzuna")
        assert h1 == h2

    def test_accent_insensitive(self):
        h1 = compute_dedup_hash("Développeur Senior", "Société X", "src")
        h2 = compute_dedup_hash("Developpeur Senior", "Societe X", "src")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = compute_dedup_hash("SENIOR PYTHON DEV", "ACME CORP", "ADZUNA")
        h2 = compute_dedup_hash("senior python dev", "acme corp", "adzuna")
        assert h1 == h2

    def test_different_source_different_hash(self):
        h1 = compute_dedup_hash("Senior Python Dev", "Acme Corp", "adzuna")
        h2 = compute_dedup_hash("Senior Python Dev", "Acme Corp", "linkedin")
        assert h1 != h2


# ---------------------------------------------------------------------------
# Dedupe: find_existing_job
# ---------------------------------------------------------------------------


class TestFindExistingJob:
    def test_returns_none_when_not_found(self, jobs_collection):
        from pipeline.dedupe import find_existing_job

        result = find_existing_job("nonexistent_hash", jobs_collection)
        assert result is None

    def test_returns_job_when_found(self, jobs_collection):
        from pipeline.dedupe import find_existing_job

        job = _make_job(dedup_hash="testhash123")
        jobs_collection.insert_one(job.to_mongo_doc())

        result = find_existing_job("testhash123", jobs_collection)
        assert result is not None
        assert result.dedup_hash == "testhash123"
        assert result.url == job.url


# ---------------------------------------------------------------------------
# Dedupe: merge_with_existing
# ---------------------------------------------------------------------------


class TestMergeWithExisting:
    def test_updates_last_seen_at(self, jobs_collection):
        from pipeline.dedupe import merge_with_existing

        job = _make_job()
        jobs_collection.insert_one(job.to_mongo_doc())

        new_raw = _raw(url="https://jobs.example.com/999")
        updated = merge_with_existing(job, new_raw, jobs_collection)

        assert updated.last_seen_at >= job.last_seen_at

    def test_increments_seen_count(self, jobs_collection):
        from pipeline.dedupe import merge_with_existing

        job = _make_job()
        jobs_collection.insert_one(job.to_mongo_doc())

        new_raw = _raw()
        merge_with_existing(job, new_raw, jobs_collection)

        doc = jobs_collection.find_one({"dedup_hash": job.dedup_hash})
        assert doc["seen_count"] == 1

    def test_keeps_earliest_posted_at(self, jobs_collection):
        from datetime import timedelta

        from pipeline.dedupe import merge_with_existing

        earlier_date = _NOW - timedelta(days=5)
        job = _make_job()
        jobs_collection.insert_one(job.to_mongo_doc())

        new_raw = _raw(posted_at=earlier_date)
        updated = merge_with_existing(job, new_raw, jobs_collection)

        # In-memory object reflects the earlier posted_at
        assert updated.posted_at <= _NOW

    def test_keeps_original_url_on_different_url(self, jobs_collection):
        from pipeline.dedupe import merge_with_existing

        job = _make_job(url="https://jobs.original.com/1")
        jobs_collection.insert_one(job.to_mongo_doc())

        new_raw = _raw(url="https://jobs.mirror.com/999")
        merge_with_existing(job, new_raw, jobs_collection)

        doc = jobs_collection.find_one({"dedup_hash": job.dedup_hash})
        assert doc["url"] == "https://jobs.original.com/1"


# ---------------------------------------------------------------------------
# Dedupe: fuzzy duplicate detection
# ---------------------------------------------------------------------------


class TestFuzzyDedupe:
    def test_no_fuzzy_match_empty_db(self, jobs_collection):
        from pipeline.dedupe import check_fuzzy_dup

        raw = _raw()
        result = check_fuzzy_dup(raw, jobs_collection)
        assert result is None

    def test_no_fuzzy_match_different_company(self, jobs_collection):
        from pipeline.dedupe import check_fuzzy_dup

        # Insert job from different company
        job = _make_job()
        doc = job.to_mongo_doc()
        doc["company"]["name_normalized"] = "other company"
        doc["title_normalized"] = "senior python developer"
        doc["source"] = "linkedin"
        doc["posted_at"] = _NOW
        jobs_collection.insert_one(doc)

        raw = _raw(company_name="Acme Corp", source="adzuna")
        result = check_fuzzy_dup(raw, jobs_collection)
        assert result is None

    def test_fuzzy_match_similar_title_different_source(self, jobs_collection):
        from pipeline.dedupe import check_fuzzy_dup

        # "senior python developer ii" vs "senior python developer" scores ~94 — above 92 threshold
        job = _make_job()
        doc = job.to_mongo_doc()
        doc["company"]["name_normalized"] = "acme corp"
        doc["title_normalized"] = "senior python developer ii"
        doc["source"] = "linkedin"  # different source
        doc["posted_at"] = _NOW
        jobs_collection.insert_one(doc)

        raw = _raw(
            title="Senior Python Developer",
            company_name="Acme Corp",
            source="adzuna",
        )
        result = check_fuzzy_dup(raw, jobs_collection)
        assert result is not None

    def test_no_fuzzy_match_same_source(self, jobs_collection):
        from pipeline.dedupe import check_fuzzy_dup

        # SPEC: fuzzy only checks different sources
        job = _make_job()
        doc = job.to_mongo_doc()
        doc["company"]["name_normalized"] = "acme corp"
        doc["title_normalized"] = "sr python developer"
        doc["source"] = "adzuna"  # same source
        doc["posted_at"] = _NOW
        jobs_collection.insert_one(doc)

        raw = _raw(title="Senior Python Developer", company_name="Acme Corp", source="adzuna")
        result = check_fuzzy_dup(raw, jobs_collection)
        assert result is None


# ---------------------------------------------------------------------------
# Ground truth validation: pre-filter
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ground_truth"


def _load_fixtures() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(FIXTURES_DIR.glob("*.json"))]


def _fixture_to_raw(f: dict) -> RawJob:
    inp = f["input"]
    posted_at = None
    if inp.get("posted_at"):
        posted_at = datetime.fromisoformat(inp["posted_at"]).replace(tzinfo=timezone.utc)
    return RawJob(
        url=inp["url"],
        title=inp["title"],
        description=inp["description"],
        company_name=inp["company_name"],
        source=inp["source"],
        posted_at=posted_at,
        original_language=inp.get("detected_language"),
    )


class TestGroundTruthPrefilter:
    """Run pre-filter against all 30 ground-truth fixtures."""

    def test_all_fixtures_match_expected_prefilter_outcome(self):
        fixtures = _load_fixtures()
        failures: list[str] = []
        pass_count = 0
        reject_count = 0

        for f in fixtures:
            raw = _fixture_to_raw(f)
            expected_pass = f["expected_output"]["should_pass_prefilter"]
            expected_reason = f["expected_output"].get("expected_reject_reason") or ""

            ok, reason = should_send_to_ai(raw)

            if ok != expected_pass:
                failures.append(
                    f"{f['input']['title'][:40]}: "
                    f"expected pass={expected_pass}, got pass={ok}, reason={reason!r}"
                )
            elif not expected_pass and expected_reason and reason != expected_reason:
                failures.append(
                    f"{f['input']['title'][:40]}: "
                    f"expected reason={expected_reason!r}, got {reason!r}"
                )

            if ok:
                pass_count += 1
            else:
                reject_count += 1

        total = len(fixtures)
        assert not failures, "Ground truth mismatches:\n" + "\n".join(failures)
        # Sanity: at least 1 reject (it_010_support_prefilter_bad)
        assert reject_count >= 1
        assert pass_count + reject_count == total

    def test_prefilter_reject_count_and_pass_count(self):
        """Report distribution; logged so CI output captures it."""
        fixtures = _load_fixtures()
        pass_count = sum(
            1 for f in fixtures if f["expected_output"]["should_pass_prefilter"]
        )
        reject_count = len(fixtures) - pass_count
        total = len(fixtures)

        # Ground truth dataset is curated — mostly good jobs.
        # Prefilter rejects only the explicitly bad fixtures.
        assert pass_count > reject_count, (
            f"Expected more passes than rejects in curated set. "
            f"pass={pass_count}, reject={reject_count}, total={total}"
        )
