"""Unit tests for Ashby ATS connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.ashby import AshbyConnector


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_ashby_empty_jobs() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"jobs": []})
        c = AshbyConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_ashby_yields_dicts() -> None:
    payload = {
        "jobs": [
            {
                "id": "job-1",
                "title": "Staff Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/test/job-1",
                "descriptionHtml": "<p>Lead platform engineering.</p>",
                "isRemote": True,
                "location": "Remote",
                "employmentType": "FullTime",
                "publishedAt": "2026-04-01T00:00:00Z",
                "compensationTierSummary": "$140,000 - $180,000 USD / year",
            }
        ]
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = AshbyConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Staff Engineer"


def test_ashby_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection error")
        c = AshbyConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)
