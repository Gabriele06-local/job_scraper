"""Unit tests for Himalayas REST connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.himalayas import HimalayasConnector


def _mock_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_himalayas_empty_jobs() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"jobs": [], "totalCount": 0})
        c = HimalayasConnector()
        jobs = list(c.fetch())
    assert jobs == []


def test_himalayas_yields_dicts() -> None:
    payload = {
        "jobs": [
            {
                "title": "Senior Engineer",
                "company": {"name": "Acme"},
                "applicationUrl": "https://example.com/apply",
                "description": "Build great things.",
                "remote": True,
                "createdAt": "2026-04-01T10:00:00Z",
                "location": "Worldwide",
                "jobType": "full_time",
                "id": "abc123",
            }
        ],
        "totalCount": 1,
    }
    with patch("requests.get") as mock_get:
        # First call returns data; subsequent pages return empty.
        mock_get.side_effect = [
            _mock_response(payload),
            _mock_response({"jobs": [], "totalCount": 0}),
        ]
        c = HimalayasConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Senior Engineer"


def test_himalayas_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = HimalayasConnector()
        jobs = list(c.fetch())
    assert jobs == []
