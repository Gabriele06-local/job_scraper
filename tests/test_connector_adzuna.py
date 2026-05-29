"""Unit tests for Adzuna connector."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.adzuna import AdzunaConnector


def _mock_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_adzuna_empty_results() -> None:
    with (
        patch("requests.get") as mock_get,
        patch("connectors.adzuna.settings.adzuna_app_id", "test-id"),
        patch("connectors.adzuna.settings.adzuna_app_key", "test-key"),
    ):
        mock_get.return_value = _mock_json_response({"results": []})
        c = AdzunaConnector(keywords=["python"], countries=["gb"])
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_adzuna_yields_dicts() -> None:
    payload = {
        "results": [
            {
                "title": "Python Developer",
                "company": {"display_name": "Acme Ltd"},
                "description": "We need a Python developer.",
                "redirect_url": "https://example.com/job/1",
                "location": {"display_name": "London"},
                "created": "2026-05-01",
                "salary_min": 50000,
                "salary_max": 70000,
            }
        ]
    }
    with (
        patch("requests.get") as mock_get,
        patch("connectors.adzuna.settings.adzuna_app_id", "test-id"),
        patch("connectors.adzuna.settings.adzuna_app_key", "test-key"),
    ):
        mock_get.return_value = _mock_json_response(payload)
        c = AdzunaConnector(keywords=["python"], countries=["gb"])
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Python Developer"
    assert j["company_name"] == "Acme Ltd"
    assert j["url"] == "https://example.com/job/1"
    assert j["source"] == "Adzuna"


def test_adzuna_no_crash_on_http_error() -> None:
    with (
        patch("requests.get") as mock_get,
        patch("connectors.adzuna.settings.adzuna_app_id", "test-id"),
        patch("connectors.adzuna.settings.adzuna_app_key", "test-key"),
    ):
        mock_get.side_effect = Exception("timeout")
        c = AdzunaConnector(keywords=["python"], countries=["gb"])
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []
