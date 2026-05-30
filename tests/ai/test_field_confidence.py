"""Per-field confidence parsing + mapping (SPEC 05 §4.3)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from ai.classifier import GroqClassifier

_BASE_OUTPUT = {
    "skills": ["Python"],
    "category": "software-engineering",
    "seniority": "senior",
    "role_family": "backend",
    "employment_type": "full_time",
    "remote_mode": "remote",
    "salary_min": None,
    "salary_max": None,
    "currency": None,
    "languages_required": [],
    "quality_flags": ["clear_jd"],
    "confidence": 0.9,
}

_JOB = {
    "title": "Senior Backend Engineer",
    "company_name": "Acme",
    "location_raw": "Remote",
    "detected_language": "en",
    "description": "desc",
    "url": "https://x/1",
}


def _client(output: dict) -> MagicMock:
    choice = MagicMock()
    choice.message.content = json.dumps(output)
    usage = MagicMock()
    usage.prompt_tokens = 50
    usage.completion_tokens = 20
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def _classifier(output: dict) -> GroqClassifier:
    clf = GroqClassifier(client=_client(output))
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]
    return clf


def test_field_confidence_parsed_and_clamped():
    out = dict(
        _BASE_OUTPUT,
        field_confidence={
            "salary": 0.2,
            "remote_mode": 1.5,  # clamp to 1.0
            "seniority": 0.8,
            "company_quality": -0.3,  # clamp to 0.0
            "technologies": 0.95,
        },
    )
    result = _classifier(out).classify(_JOB)
    assert result is not None
    fc = result.field_confidence
    assert fc is not None
    assert fc["remote_mode"] == 1.0
    assert fc["company_quality"] == 0.0
    assert fc["salary"] == 0.2


def test_field_confidence_absent_is_none():
    result = _classifier(_BASE_OUTPUT).classify(_JOB)
    assert result is not None
    assert result.field_confidence is None


def test_field_confidence_unknown_keys_dropped():
    out = dict(_BASE_OUTPUT, field_confidence={"bogus": 0.9, "salary": 0.5})
    result = _classifier(out).classify(_JOB)
    assert result is not None
    assert result.field_confidence == {"salary": 0.5}


def test_field_confidence_persists_in_mongo_doc():
    from datetime import datetime, timezone

    from models.job import (
        Job,
        JobClassification,
        JobCompany,
        JobContent,
        JobSource,
        Language,
    )

    cl = JobClassification(field_confidence={"salary": 0.7})
    job = Job(
        url="https://x/2",
        dedup_hash="h",
        source_info=JobSource(source="adzuna"),
        content=JobContent(
            title="t", title_normalized="t", description="d", language=Language.EN
        ),
        company=JobCompany(name="Acme", name_normalized="acme"),
        posted_at=datetime.now(tz=timezone.utc),
        classification=cl,
    )
    doc = job.to_mongo_doc()
    assert doc["ai_field_confidence"] == {"salary": 0.7}

    # Round-trips back through from_mongo_doc.
    restored = Job.from_mongo_doc(doc)
    assert restored.classification.field_confidence == {"salary": 0.7}
