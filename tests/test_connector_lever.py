"""Unit tests for Lever ATS connector."""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from connectors.lever import LeverConnector


def _mock_httpx_response(payload: list | dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "", request=MagicMock(), response=resp
        )
    return resp


def test_lever_empty_postings() -> None:
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_httpx_response([]))
        c = LeverConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_lever_yields_dicts() -> None:
    payload = [
        {
            "id": "abc-123",
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.lever.co/test/abc-123",
            "descriptionPlain": "Build microservices with Go.",
            "categories": {
                "team": "Engineering",
                "location": "Remote",
                "commitment": "Full-time",
            },
            "createdAt": 1712000000000,
        }
    ]
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_httpx_response(payload))
        c = LeverConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Backend Engineer"


def test_lever_no_crash_on_http_error() -> None:
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
        c = LeverConnector()
        c._companies = [{"name": "Test", "slug": "test"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)
