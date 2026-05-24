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


def _empty_batch() -> dict:
    return {"batch": [], "offset": 0, "page_size": 100, "total_count": 0}


def test_faang_watch_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(_empty_batch())
        c = FaangWatchConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_faang_watch_yields_dicts() -> None:
    payload = {
        "batch": [
            {
                "categories": ["Software Engineering"],
                "company": "Amazon",
                "company_url": "https://amazon.jobs/en/jobs/2874376/front-end-engineer-ii-aws-professional-services",
                "description": "### Description\n\nOur team owns...",
                "earliest_date": "2025-01-15T20:45:23",
                "job_id": "jnJpGGKAyibw9Xg6iujFCv",
                "locations": ["US, VA, Arlington"],
                "parsed_locations": [
                    {"city": "Arlington", "country": "United States", "state": None}
                ],
                "seniority": "Mid",
                "title": "Front End Engineer II, AWS Professional Services",
            }
        ],
        "offset": 0,
        "page_size": 100,
        "total_count": 1,
    }
    side_effects: list = []
    # First page populated, second empty terminates per-company loop.
    for _ in range(10):
        side_effects.append(_mock_response(payload))
        side_effects.append(_mock_response(_empty_batch()))
    with patch("requests.get") as mock_get:
        mock_get.side_effect = side_effects
        c = FaangWatchConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    j = jobs[0]
    assert j["title"] == "Front End Engineer II, AWS Professional Services"
    assert j["company_name"] == "Amazon"
    assert j["url"].startswith("https://amazon.jobs/")
    assert j["source"] == "faang.watch"
    assert j["location_raw"] == "Arlington, United States"
    assert j["external_id"] == "jnJpGGKAyibw9Xg6iujFCv"


def test_faang_watch_skips_incomplete_item() -> None:
    payload = {
        "batch": [
            {"job_id": "x", "title": "no url"},  # missing company + url
        ],
        "total_count": 1,
    }
    with patch("requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(payload), _mock_response(_empty_batch())] * 10
        c = FaangWatchConnector()
        c._scraper._api_key = "test-key"
        jobs = list(c.fetch())
    assert jobs == []


def test_faang_watch_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = FaangWatchConnector()
        jobs = list(c.fetch())
    assert jobs == []
