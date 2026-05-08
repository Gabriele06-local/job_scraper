"""Unit tests for Reed REST connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.reed import ReedConnector


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_reed_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"results": []})
        c = ReedConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_reed_yields_dicts() -> None:
    payload = {
        "results": [
            {
                "jobId": 123,
                "jobTitle": "Python Developer",
                "employerName": "Reed Corp",
                "jobUrl": "https://reed.co.uk/jobs/123",
                "jobDescription": "Build Python microservices.",
                "locationName": "London",
                "minimumSalary": 60000,
                "maximumSalary": 80000,
                "expirationDate": "2026-12-31",
                "date": "2026-04-01T00:00:00Z",
            }
        ]
    }
    # Alternate data + empty pages to stop keyword pagination.
    side_effects = []
    for _ in range(12):  # 12 keywords
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response({"results": []}))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = ReedConnector()
        c._scraper._api_key = "test-key"  # bypass api key guard
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Python Developer"


def test_reed_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = ReedConnector()
        jobs = list(c.fetch())
    assert jobs == []
