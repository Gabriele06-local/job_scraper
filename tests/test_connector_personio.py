"""Unit tests for Personio ATS XML connector — published_at extraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from connectors.personio import PersonioConnector


def _xml_response(xml: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = xml.encode()
    resp.raise_for_status.return_value = None
    return resp


_PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workboard>
  <position>
    <id>123</id>
    <name>Software Engineer</name>
    <office>Berlin</office>
    <jobDescriptions><![CDATA[We are hiring a software engineer.]]></jobDescriptions>
    <createdAt>{created}</createdAt>
  </position>
</workboard>"""


def test_personio_parses_created_at() -> None:
    xml = _PERSONIO_XML.format(created="2026-04-15T10:00:00Z")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = PersonioConnector()
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert all(j["published_at"] is not None for j in jobs)


def test_personio_parses_created_at_without_z() -> None:
    xml = _PERSONIO_XML.format(created="2026-04-15T10:00:00")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = PersonioConnector()
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert all(j["published_at"] is not None for j in jobs)


def test_personio_empty_when_no_date() -> None:
    xml = _PERSONIO_XML.format(created="")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = PersonioConnector()
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert all(j["published_at"] is None for j in jobs)


def test_personio_no_crash_on_xml_parse_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response("not xml")
        c = PersonioConnector()
        jobs = list(c.fetch())
    assert jobs == []


def test_personio_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = PersonioConnector()
        jobs = list(c.fetch())
    assert jobs == []
