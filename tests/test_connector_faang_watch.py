"""Unit tests for the faang.watch (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.faang_watch import FaangWatchConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_faang_watch_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = FaangWatchConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_faang_watch_yields_dicts() -> None:
    payload = [
        {
            "id": "g-1",
            "title": "Senior SWE, Search",
            "company": "Google",
            "url": "https://careers.google.com/jobs/results/1/",
            "description": "Improve Google search ranking.",
            "location": "Mountain View, CA",
            "date_posted": "2026-05-12T00:00:00Z",
        }
    ]
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = FaangWatchConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Senior SWE, Search"
    assert jobs[0]["company_name"] == "Google"
    assert jobs[0]["source"] == "faang.watch"


def test_faang_watch_flattens_bucket_dict() -> None:
    # /seniority returns a bucket dict like {senior:[...], mid:[...]}.
    payload = {
        "senior": [
            {
                "id": "s-1",
                "title": "Staff Engineer",
                "company": "Meta",
                "url": "https://meta.com/jobs/s-1",
            }
        ],
        "mid": [
            {
                "id": "m-1",
                "title": "Software Engineer",
                "company": "Apple",
                "url": "https://apple.com/jobs/m-1",
            }
        ],
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = FaangWatchConnector()
        c._scraper._api_key = "test-key"
        jobs = list(c.fetch())
    assert len(jobs) == 2
    assert {j["title"] for j in jobs} == {"Staff Engineer", "Software Engineer"}


def test_faang_watch_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = FaangWatchConnector()
        jobs = list(c.fetch())
    assert jobs == []
