"""Unit tests for Jobicy connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.jobicy import JobicyConnector


def _mock_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_jobicy_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response({"success": True, "jobs": []})
        c = JobicyConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_jobicy_yields_dicts() -> None:
    payload = {
        "success": True,
        "jobs": [
            {
                "jobTitle": "Remote Python Developer",
                "companyName": "CloudCorp",
                "jobDescription": "Build cloud infrastructure with Python.",
                "url": "https://jobicy.com/job/1",
                "pubDate": "2026-05-01 12:00:00",
                "jobGeo": "Worldwide",
                "jobType": "full-time",
                "annualSalaryMin": 60000,
                "annualSalaryMax": 90000,
            }
        ],
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response(payload)
        c = JobicyConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Remote Python Developer"
    assert j["company_name"] == "CloudCorp"
    assert j["url"] == "https://jobicy.com/job/1"
    assert j["source"] == "Jobicy"


def test_jobicy_api_failure_returns_empty() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response({"success": False, "message": "rate limited"})
        c = JobicyConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_jobicy_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = JobicyConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []
