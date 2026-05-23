"""Smoke tests for the connectors package.

Verifies:
- BaseConnector interface (source_name, source_type, rate_limit_seconds)
- Registry completeness and enabled flags
- get_enabled_connectors() returns correct count
- Each connector's fetch() is callable and yields dicts (mocked HTTP)
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

from connectors import REGISTRY, SourceType, get_enabled_connectors
from connectors.adzuna import AdzunaConnector
from connectors.arbeitnow import ArbeitnowConnector
from connectors.base import BaseConnector
from connectors.iprogrammatori import IProgrammatoriConnector
from connectors.jobicy import JobicyConnector
from connectors.jooble import JoobleConnector
from connectors.remoteok import RemoteOKConnector
from connectors.rss import RSSConnector
from database.repository import _LEGACY_ENABLED_SLUGS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_REGISTRY_KEYS = {
    # legacy (DB-fallback enabled)
    "adzuna",
    "jooble",
    "iprogrammatori",
    "arbeitnow",
    "remoteok",
    "jobicy",
    "rss",
    "himalayas",
    "remotive",
    "themuse",
    "reed",
    "jsearch",
    "greenhouse",
    "lever",
    "ashby",
    "personio",
    # new RapidAPI providers (DB-gated, disabled by default in seed)
    "active_jobs_db",
}

# get_enabled_connectors() consults the DB-backed `providers` collection.
# On an un-seeded test environment the lookup falls back to the legacy
# whitelist (see database.repository.is_provider_enabled), so the expected
# count equals the size of that whitelist.
_ENABLED_COUNT = len(_LEGACY_ENABLED_SLUGS)


def _mock_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _mock_html_response(html: str = "<html><body></body></html>") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = html.encode()
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


def _mock_xml_response(xml: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = xml.encode()
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Interface / registry tests
# ---------------------------------------------------------------------------


def test_registry_completeness() -> None:
    assert set(REGISTRY.keys()) == _EXPECTED_REGISTRY_KEYS


def test_all_connectors_enabled() -> None:
    for name, entry in REGISTRY.items():
        assert entry.enabled, f"{name}: must be enabled (remove disabled connectors instead)"


def test_all_connectors_have_required_class_attrs() -> None:
    for name, entry in REGISTRY.items():
        cls = entry.cls
        assert hasattr(cls, "source_name"), f"{name}: missing source_name"
        assert hasattr(cls, "source_type"), f"{name}: missing source_type"
        assert hasattr(cls, "rate_limit_seconds"), f"{name}: missing rate_limit_seconds"
        assert cls.source_type in SourceType, f"{name}: invalid source_type"
        assert isinstance(cls.rate_limit_seconds, float), f"{name}: rate_limit_seconds not float"


def test_all_connectors_inherit_base() -> None:
    for name, entry in REGISTRY.items():
        assert issubclass(entry.cls, BaseConnector), f"{name}: not a BaseConnector"


def test_get_enabled_connectors_count() -> None:
    connectors = get_enabled_connectors()
    assert len(connectors) == _ENABLED_COUNT


def test_get_enabled_connectors_all_base() -> None:
    for c in get_enabled_connectors():
        assert isinstance(c, BaseConnector)


def test_fetch_returns_iterator() -> None:
    """fetch() must return an Iterator for all registered connectors."""
    for c in get_enabled_connectors():
        result = c.fetch()
        assert isinstance(result, Iterator)


# ---------------------------------------------------------------------------
# Per-connector smoke: mocked HTTP, fetch yields dicts
# ---------------------------------------------------------------------------


def test_adzuna_fetch_empty_response() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response({"results": []})
        c = AdzunaConnector(keywords=["python"], countries=["gb"])
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_adzuna_fetch_with_data() -> None:
    payload = {
        "results": [
            {
                "title": "Python Developer",
                "company": {"display_name": "ACME"},
                "redirect_url": "https://example.com/job/1",
                "description": "Great job",
                "location": {"display_name": "London"},
                "created": "2026-01-01",
                "salary_min": 50000,
                "salary_max": 70000,
            }
        ]
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response(payload)
        c = AdzunaConnector(keywords=["python"], countries=["gb"])
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Developer"
    assert isinstance(jobs[0], dict)


def test_arbeitnow_fetch_empty_response() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response({"data": []})
        c = ArbeitnowConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_iprogrammatori_fetch_empty_response() -> None:
    xml = b"<?xml version='1.0'?><jobs></jobs>"
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_xml_response(xml.decode())
        c = IProgrammatoriConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_jobicy_fetch_empty_response() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response({"success": True, "jobs": []})
        c = JobicyConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_jooble_fetch_no_api_key() -> None:
    # Without API key, scraper returns [] immediately without HTTP call
    c = JoobleConnector(keywords=["python"], languages=["it"])
    c._scraper.api_key = None  # force missing key
    jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)
    assert jobs == []


def test_remoteok_fetch_empty_response() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response([{"legal": "notice"}])
        c = RemoteOKConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)


def test_remoteok_fetch_with_data() -> None:
    payload = [
        {"legal": "notice"},
        {
            "position": "Python Engineer",
            "company": "RemoteCo",
            "description": "Build things remotely",
            "url": "https://remoteok.com/job/1",
            "tags": ["python"],
            "date": "2026-01-01T00:00:00+00:00",
            "location": "Worldwide",
        },
    ]
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_json_response(payload)
        c = RemoteOKConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Engineer"


def test_rss_fetch_empty_feed() -> None:
    xml = (
        "<?xml version='1.0'?>"
        "<rss version='2.0'><channel><title>Test</title></channel></rss>"
    )
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_xml_response(xml)
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(itertools.islice(c.fetch(), 5))
    assert isinstance(jobs, list)
