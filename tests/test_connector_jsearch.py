"""Unit tests for JSearch (Apify-proxied) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.jsearch import JSearchConnector


def _mock_response(payload: list) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_jsearch_empty_results() -> None:
    with patch("requests.post") as mock_post:
        mock_post.return_value = _mock_response([])
        c = JSearchConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_jsearch_yields_dicts() -> None:
    payload = [
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
    with patch("requests.post") as mock_post:
        mock_post.return_value = _mock_response(payload)
        c = JSearchConnector()
        c._scraper._api_token = "test-token"  # bypass token guard
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


def test_jsearch_no_crash_on_http_error() -> None:
    with patch("requests.post") as mock_post:
        mock_post.side_effect = Exception("connection refused")
        c = JSearchConnector()
        jobs = list(c.fetch())
    assert jobs == []
