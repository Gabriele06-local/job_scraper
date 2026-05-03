"""Pre-filter stage — rejects offers before expensive AI classification.

Rules (SPEC 02 §3): all must pass, first failure short-circuits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from urllib.parse import urlparse

import structlog

from models.job import Language, RawJob
from pipeline.language_detector import detect_language

logger = structlog.get_logger(__name__)

_SUPPORTED_LANGUAGES = {
    Language.EN, Language.IT, Language.ES, Language.DE, Language.FR, Language.PT
}

_INVALID_COMPANY_NAMES = {"", "unknown", "n/a", "none"}

MIN_DESCRIPTION_LEN = 150
MAX_AGE_DAYS = 60


class RejectReason(str, Enum):
    DESCRIPTION_TOO_SHORT = "DESCRIPTION_TOO_SHORT"
    MISSING_REQUIRED_FIELDS = "MISSING_REQUIRED_FIELDS"
    EXPIRED_LISTING = "EXPIRED_LISTING"
    LANGUAGE_NOT_SUPPORTED = "LANGUAGE_NOT_SUPPORTED"
    SUSPECTED_SPAM = "SUSPECTED_SPAM"
    OTHER = "OTHER"


def should_send_to_ai(job_raw: RawJob) -> tuple[bool, str]:
    """Check if offer passes all pre-filter rules.

    Returns (True, "") on pass.
    Returns (False, reason) on reject; logs structured reject event.
    """
    reason = _check(job_raw)
    if reason:
        logger.info(
            "prefilter.rejected",
            url=job_raw.url,
            title=job_raw.title[:80] if job_raw.title else "",
            company=job_raw.company_name,
            source=job_raw.source,
            reason=reason,
        )
        return False, reason
    return True, ""


def _check(job_raw: RawJob) -> str:
    """Return reject reason string or empty string if all rules pass."""
    # Missing title
    if not job_raw.title or not job_raw.title.strip():
        return RejectReason.MISSING_REQUIRED_FIELDS.value

    # Missing company
    if not job_raw.company_name or job_raw.company_name.strip().lower() in _INVALID_COMPANY_NAMES:
        return RejectReason.MISSING_REQUIRED_FIELDS.value

    # Invalid URL
    if not _is_valid_url(job_raw.url):
        return RejectReason.MISSING_REQUIRED_FIELDS.value

    # Missing posted_at
    if job_raw.posted_at is None:
        return RejectReason.MISSING_REQUIRED_FIELDS.value

    # Listing too old
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    posted = job_raw.posted_at
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    if posted < cutoff:
        return RejectReason.EXPIRED_LISTING.value

    # Description too short
    if len(job_raw.description) < MIN_DESCRIPTION_LEN:
        return RejectReason.DESCRIPTION_TOO_SHORT.value

    # Language check
    if not _is_language_supported(job_raw):
        return RejectReason.LANGUAGE_NOT_SUPPORTED.value

    # Suspected spam: title is all-caps with > 5 words (common spam pattern)
    if _is_suspected_spam(job_raw):
        return RejectReason.SUSPECTED_SPAM.value

    return ""


def _is_valid_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _is_language_supported(job_raw: RawJob) -> bool:
    """Check detected language; fall back to scraper-declared original_language."""
    # Trust scraper-declared language if it's in supported set (signal trust per SPEC)
    if job_raw.original_language:
        try:
            declared = Language(job_raw.original_language.lower())
            if declared in _SUPPORTED_LANGUAGES:
                return True
        except ValueError:
            pass

    detected, _conf = detect_language(job_raw.description[:2000])
    return detected in _SUPPORTED_LANGUAGES


def _is_suspected_spam(job_raw: RawJob) -> bool:
    """Title all-caps with >= 5 words is a reliable spam signal."""
    words = job_raw.title.split()
    if len(words) < 5:
        return False
    return job_raw.title == job_raw.title.upper() and any(c.isalpha() for c in job_raw.title)
