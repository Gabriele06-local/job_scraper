"""Unit tests for JSearch (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.jsearch import JSearchConnector


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_jsearch_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"data": []})
        c = JSearchConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_jsearch_yields_dicts() -> None:
    payload = {
        "data": [
            {
                "job_id": "abc123",
                "job_title": "Senior Backend Engineer",
                "employer_name": "Acme Corp",
                "job_apply_link": "https://example.com/jobs/abc123",
                "job_description": "Build scalable distributed systems in Python.",
                "job_city": "Austin",
                "job_country": "US",
                "job_min_salary": 120000,
                "job_max_salary": 160000,
                "job_salary_currency": "USD",
                "job_posted_at_datetime_utc": "2026-05-01T00:00:00Z",
            }
        ]
    }
    # Alternate one populated page then empty per keyword to terminate pagination quickly.
    side_effects: list = []
    for _ in range(12):
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response({"data": []}))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = JSearchConnector()
        c._scraper._api_key = "test-key"  # bypass key guard
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Senior Backend Engineer"
    assert jobs[0]["company_name"] == "Acme Corp"
    assert jobs[0]["url"] == "https://example.com/jobs/abc123"
    assert jobs[0]["source"] == "JSearch"
    assert jobs[0]["original_language"] == "en"
    assert jobs[0]["location_raw"] == "Austin, US"
    assert jobs[0]["salary_min"] == 120000
    assert jobs[0]["external_id"] == "abc123"
    # posted_at parsed to tz-aware datetime so pre-filter accepts it
    from datetime import datetime, timezone

    assert isinstance(jobs[0]["published_at"], datetime)
    assert jobs[0]["published_at"].tzinfo is not None
    assert jobs[0]["published_at"] == datetime(2026, 5, 1, tzinfo=timezone.utc)


def test_jsearch_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = JSearchConnector()
        jobs = list(c.fetch())
    assert jobs == []
