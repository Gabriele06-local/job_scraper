"""Unit tests for The Muse REST connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.themuse import TheMuseConnector


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_themuse_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"results": []})
        c = TheMuseConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_themuse_yields_dicts() -> None:
    payload = {
        "results": [
            {
                "id": 1,
                "name": "Software Engineer",
                "company": {"name": "TechCorp"},
                "refs": {"landing_page": "https://themuse.com/jobs/1"},
                "contents": "<p>Build cool things in Python.</p>",
                "locations": [{"name": "New York, NY"}],
                "publication_date": "2026-04-01T00:00:00Z",
            }
        ]
    }
    with patch("requests.get") as mock_get:
        # All pages return data once, then empty on second call per category.
        mock_get.return_value = _mock_response({"results": []})
        mock_get.side_effect = [
            _mock_response(payload),
            _mock_response({"results": []}),
        ] * 50  # enough for all category×level combos
        c = TheMuseConnector()
        c._scraper._api_key = "test-key"  # bypass api key guard
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Software Engineer"


def test_themuse_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = TheMuseConnector()
        jobs = list(c.fetch())
    assert jobs == []
