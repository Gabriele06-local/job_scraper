"""Unit tests for Remotive REST connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.remotive import RemotiveConnector


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_remotive_empty_jobs() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"jobs": []})
        c = RemotiveConnector()
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_remotive_yields_dicts() -> None:
    payload = {
        "jobs": [
            {
                "title": "Backend Engineer",
                "company_name": "StartupCo",
                "url": "https://remotive.com/remote-jobs/software-dev/1",
                "description": "Python + Django.",
                "job_type": "full_time",
                "candidate_required_location": "Worldwide",
                "publication_date": "2026-04-01T00:00:00",
                "tags": ["python", "django"],
                "salary": "$80k-$100k",
                "id": 999,
            }
        ]
    }
    with patch("requests.get") as mock_get, patch("time.sleep"):
        mock_get.return_value = _mock_response(payload)
        c = RemotiveConnector()
        # Scraper iterates 7 categories → up to 7 jobs; islice limits to 5.
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Backend Engineer"
    assert isinstance(jobs[0], dict)


def test_remotive_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = RemotiveConnector()
        jobs = list(c.fetch())
    assert jobs == []
