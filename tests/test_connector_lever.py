"""Unit tests for Lever ATS connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.lever import LeverConnector


def _mock_response(payload: list | dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_lever_empty_postings() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = LeverConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_lever_yields_dicts() -> None:
    payload = [
        {
            "id": "abc-123",
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.lever.co/test/abc-123",
            "descriptionPlain": "Build microservices with Go.",
            "categories": {
                "team": "Engineering",
                "location": "Remote",
                "commitment": "Full-time",
            },
            "createdAt": 1712000000000,
        }
    ]
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = LeverConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Backend Engineer"


def test_lever_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = LeverConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)
