"""Unit tests for Jooble API connector — no verify=False bypass."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from connectors.jooble import JoobleConnector


def _mock_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_jooble_no_api_key_returns_empty() -> None:
    c = JoobleConnector(keywords=["python"], languages=["it"])
    c._scraper.api_key = None
    jobs = list(c.fetch())
    assert jobs == []


def _make_connector(
    keywords: list[str] | None = None,
    languages: list[str] | None = None,
) -> JoobleConnector:
    c = JoobleConnector(keywords=keywords or ["python"], languages=languages or ["en"])
    c._scraper.api_key = "test-key"
    return c


def test_jooble_yields_dicts() -> None:
    payload = {
        "jobs": [
            {
                "title": "Python Developer",
                "company": "TechCorp",
                "snippet": "We are looking for a Python developer with experience in Django.",
                "link": "https://example.com/job/1",
                "location": "Milan",
                "source": "Indeed",
                "updated": "2026-04-20T12:00:00Z",
            }
        ]
    }
    with patch("requests.post") as mock_post:
        mock_post.return_value = _mock_response(payload)
        c = _make_connector(languages=["it"])
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    j = jobs[0]
    assert j["title"] == "Python Developer"
    assert j["company_name"] == "TechCorp"


def test_jooble_skips_closed_jobs() -> None:
    payload = {
        "jobs": [
            {
                "title": "Closed Job",
                "company": "OldCo",
                "snippet": "This job is closed.",
                "link": "https://example.com/job/1?closedJob=true",
                "location": "Rome",
                "source": "Indeed",
                "updated": "2026-04-20T12:00:00Z",
            }
        ]
    }
    with patch("requests.post") as mock_post:
        mock_post.return_value = _mock_response(payload)
        c = _make_connector(languages=["it"])
        jobs = list(c.fetch())
    assert jobs == []


def test_jooble_no_crash_on_http_error() -> None:
    with patch("requests.post") as mock_post:
        mock_post.side_effect = Exception("timeout")
        c = _make_connector(languages=["it"])
        jobs = list(c.fetch())
    assert jobs == []


def test_jooble_does_not_use_verify_false() -> None:
    """Regression: Jooble must NOT pass verify=False to requests.post."""
    payload = {
        "jobs": [
            {
                "title": "Test",
                "company": "TestCo",
                "link": "https://example.com/j",
                "snippet": "A",
            }
        ]
    }
    with patch("requests.post") as mock_post:
        mock_post.return_value = _mock_response(payload)
        c = _make_connector()
        list(c.fetch())
        _call_kwargs = mock_post.call_args[1]
        assert "verify" not in _call_kwargs or _call_kwargs.get("verify") is not False
