"""Unit tests for RemoteOK connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.remoteok import RemoteOKConnector


def _mock_json_response(payload: list | dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_remoteok_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response([{"legal": "notice"}])
        c = RemoteOKConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_remoteok_yields_dicts() -> None:
    payload = [
        {"legal": "notice"},
        {
            "position": "Python Engineer",
            "company": "RemoteCo",
            "description": "Build things remotely with Python.",
            "url": "https://remoteok.com/job/1",
            "tags": ["python"],
            "date": "2026-05-01T00:00:00+00:00",
            "location": "Worldwide",
        },
    ]
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response(payload)
        c = RemoteOKConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Python Engineer"
    assert j["company_name"] == "RemoteCo"
    assert j["url"] == "https://remoteok.com/job/1"
    assert j["source"] == "RemoteOK"


def test_remoteok_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = RemoteOKConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []
