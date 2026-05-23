"""Unit tests for the Startup Jobs (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.startup_jobs import StartupJobsConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_startup_jobs_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = StartupJobsConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_startup_jobs_yields_dicts() -> None:
    payload = [
        {
            "id": "su-42",
            "title": "Founding Engineer",
            "organization": "FluxAI",
            "url": "https://wellfound.com/jobs/42",
            "description": "Build the future of AI.",
            "location": "Remote",
            "date_posted": "2026-05-10T00:00:00Z",
        }
    ]
    side_effects: list = []
    for _ in range(20):
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response([]))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = StartupJobsConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Founding Engineer"
    assert jobs[0]["company_name"] == "FluxAI"
    assert jobs[0]["source"] == "Startup Jobs"


def test_startup_jobs_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = StartupJobsConnector()
        jobs = list(c.fetch())
    assert jobs == []
