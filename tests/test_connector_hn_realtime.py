"""Unit tests for the HN Real-Time Jobs (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.hn_realtime import HNRealtimeConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_hn_realtime_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = HNRealtimeConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_hn_realtime_yields_dicts() -> None:
    payload = [
        {
            "id": "hnrt-100",
            "title": "Senior Platform Engineer",
            "company": "Vercel",
            "url": "https://news.ycombinator.com/item?id=100",
            "description": "Build edge infra.",
            "location": "Remote",
            "time": 1740000000,
        }
    ]
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = HNRealtimeConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Senior Platform Engineer"
    assert jobs[0]["company_name"] == "Vercel"
    assert jobs[0]["source"] == "HN Real-Time Jobs"


def test_hn_realtime_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = HNRealtimeConnector()
        jobs = list(c.fetch())
    assert jobs == []
