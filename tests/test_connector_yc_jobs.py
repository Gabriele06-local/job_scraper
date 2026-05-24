"""Unit tests for the Y Combinator Jobs (RapidAPI) connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.yc_jobs import YCJobsConnector


def _mock_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_yc_jobs_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        c = YCJobsConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_yc_jobs_yields_dicts() -> None:
    payload = [
        {
            "id": "yc-7",
            "title": "Founding Backend Engineer",
            "company_name": "Stripe",
            "url": "https://www.workatastartup.com/jobs/7",
            "description": "Build payment infra at scale.",
            "location": "San Francisco",
            "date_posted": "2026-05-20T00:00:00Z",
        }
    ]
    with patch("requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(payload), _mock_response([])]
        c = YCJobsConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert jobs[0]["title"] == "Founding Backend Engineer"
    assert jobs[0]["company_name"] == "Stripe"
    assert jobs[0]["source"] == "Y Combinator Jobs"


def test_yc_jobs_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = YCJobsConnector()
        jobs = list(c.fetch())
    assert jobs == []
