"""Unit tests for Arbeitnow connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.arbeitnow import ArbeitnowConnector


def _mock_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_arbeitnow_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response({"data": []})
        c = ArbeitnowConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_arbeitnow_yields_dicts() -> None:
    payload = {
        "data": [
            {
                "title": "Backend Engineer",
                "company_name": "Tech GmbH",
                "description": "We hire a backend engineer with Python experience.",
                "url": "https://arbeitnow.com/job/1",
                "location": "Berlin",
                "created_at": 1746000000,
                "remote": True,
            }
        ]
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response(payload)
        c = ArbeitnowConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Backend Engineer"
    assert j["company_name"] == "Tech GmbH"
    assert j["url"] == "https://arbeitnow.com/job/1"
    assert j["source"] == "Arbeitnow"


def test_arbeitnow_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection error")
        c = ArbeitnowConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []
