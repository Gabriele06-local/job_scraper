"""Unit tests for the Active Jobs DB (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.active_jobs_db import ActiveJobsDbConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_active_jobs_db_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = ActiveJobsDbConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_active_jobs_db_yields_dicts() -> None:
    payload = [
        {
            "id": "ajdb-1",
            "title": "Senior Backend Engineer",
            "organization": "Acme Corp",
            "url": "https://acme.example/jobs/1",
            "description": "Build scalable distributed systems in Python.",
            "location": "Berlin, DE",
            "date_posted": "2026-05-01T00:00:00Z",
            "salary_min": 80000,
            "salary_max": 120000,
            "currency": "EUR",
        }
    ]
    side_effects: list = []
    for _ in range(20):
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response([]))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = ActiveJobsDbConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Senior Backend Engineer"
    assert jobs[0]["company_name"] == "Acme Corp"
    assert jobs[0]["url"] == "https://acme.example/jobs/1"
    assert jobs[0]["source"] == "Active Jobs DB"
    assert jobs[0]["location_raw"] == "Berlin, DE"
    assert jobs[0]["salary_min"] == 80000
    assert jobs[0]["external_id"] == "ajdb-1"


def test_active_jobs_db_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = ActiveJobsDbConnector()
        jobs = list(c.fetch())
    assert jobs == []


def test_active_jobs_db_skips_incomplete_item() -> None:
    payload = [{"id": "x", "title": "no company", "url": "https://x"}]
    with patch("requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(payload), _mock_response([])] * 20
        c = ActiveJobsDbConnector()
        c._scraper._api_key = "test-key"
        jobs = list(c.fetch())
    assert jobs == []
