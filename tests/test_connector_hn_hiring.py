"""Unit tests for the HN 'Who is Hiring' (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.hn_hiring import HNHiringConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_hn_hiring_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = HNHiringConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_hn_hiring_yields_dicts() -> None:
    payload = [
        {
            "id": "hn-42",
            "title": "Senior Rust Engineer",
            "company": "FastCo",
            "url": "https://news.ycombinator.com/item?id=42",
            "description": "Build high-perf systems in Rust.",
            "location": "Remote",
            "time": 1740000000,
        }
    ]
    side_effects: list = []
    for _ in range(20):
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response([]))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = HNHiringConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Senior Rust Engineer"
    assert jobs[0]["company_name"] == "FastCo"
    assert jobs[0]["source"] == "HN Who is Hiring"
    assert jobs[0]["location_raw"] == "Remote"


def test_hn_hiring_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = HNHiringConnector()
        jobs = list(c.fetch())
    assert jobs == []
