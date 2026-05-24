"""Unit tests for the Workday Jobs (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.workday_jobs import WorkdayJobsConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_workday_jobs_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = WorkdayJobsConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_workday_jobs_yields_dicts() -> None:
    payload = [
        {
            "id": "wd-99",
            "title": "Staff Engineer",
            "organization": "Initech",
            "url": "https://initech.wd1.myworkdayjobs.com/wday/cxs/job/99",
            "description": "Lead distributed systems team.",
            "location": "London, UK",
            "date_posted": "2026-05-15T00:00:00Z",
        }
    ]
    side_effects: list = []
    for _ in range(20):
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response([]))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = WorkdayJobsConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Staff Engineer"
    assert jobs[0]["company_name"] == "Initech"
    assert jobs[0]["source"] == "Workday Jobs"
    assert jobs[0]["location_raw"] == "London, UK"


def test_workday_jobs_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = WorkdayJobsConnector()
        jobs = list(c.fetch())
    assert jobs == []
