"""Unit tests for Greenhouse ATS connector."""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

from connectors.greenhouse import GreenhouseConnector


def _mock_httpx_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_greenhouse_empty_jobs() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(
            return_value=_mock_httpx_response({"jobs": [], "meta": {"total": 0}})
        )
        c = GreenhouseConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_greenhouse_yields_dicts() -> None:
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Software Engineer",
                "absolute_url": "https://boards.greenhouse.io/test/jobs/1",
                "content": "<p>Build things at scale.</p>",
                "location": {"name": "San Francisco, CA"},
                "updated_at": "2026-04-01T00:00:00Z",
            }
        ]
    }
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_httpx_response(payload))
        c = GreenhouseConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) >= 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Software Engineer"


def test_greenhouse_no_crash_on_http_error() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        c = GreenhouseConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)
