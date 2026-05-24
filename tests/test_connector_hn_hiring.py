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


def _empty_page() -> dict:
    return {"items": [], "totalPages": 1}


def test_hn_hiring_empty_results() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(_empty_page())
        c = HNHiringConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_hn_hiring_yields_dicts() -> None:
    """A single HN comment with one role expands into one RawJob."""
    payload = {
        "month": "2026-05",
        "page": 1,
        "perPage": 100,
        "total": 1,
        "totalPages": 1,
        "items": [
            {
                "commentId": 47975944,
                "by": "katee",
                "commentUrl": "https://news.ycombinator.com/item?id=47975944",
                "extracted": {
                    "company": "Project Debug",
                    "locations": [{"city": "Singapore", "country": "Singapore"}],
                    "workMode": "hybrid",
                    "employmentType": "full-time",
                    "salaryFrom": None,
                    "salaryTo": None,
                    "jobs": [
                        {
                            "role": "General Engineer",
                            "keywords": ["python", "go", "kubernetes"],
                            "url": None,
                        }
                    ],
                },
            }
        ],
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = HNHiringConnector()
        c._scraper._api_key = "test-key"
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "General Engineer"
    assert j["company_name"] == "Project Debug"
    assert j["url"] == "https://news.ycombinator.com/item?id=47975944"
    assert j["source"] == "HN Who is Hiring"
    assert j["location_raw"] == "Singapore, Singapore"
    assert j["external_id"] == "47975944-0"
    assert "python" in j["description"]


def test_hn_hiring_fans_out_multi_role_comments() -> None:
    """One HN comment advertising two roles yields two RawJob rows."""
    payload = {
        "items": [
            {
                "commentId": 100,
                "commentUrl": "https://news.ycombinator.com/item?id=100",
                "extracted": {
                    "company": "Acme",
                    "locations": [],
                    "jobs": [
                        {"role": "Backend Engineer", "url": "https://acme/be"},
                        {"role": "Frontend Engineer", "url": "https://acme/fe"},
                    ],
                },
            }
        ],
        "totalPages": 1,
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        c = HNHiringConnector()
        c._scraper._api_key = "test-key"
        jobs = list(c.fetch())
    assert len(jobs) == 2
    assert {j["title"] for j in jobs} == {"Backend Engineer", "Frontend Engineer"}
    assert {j["external_id"] for j in jobs} == {"100-0", "100-1"}


def test_hn_hiring_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        c = HNHiringConnector()
        jobs = list(c.fetch())
    assert jobs == []
