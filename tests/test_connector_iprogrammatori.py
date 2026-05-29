"""Unit tests for IProgrammatori connector (XML/RSS feed)."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.iprogrammatori import IProgrammatoriConnector


def _mock_xml_response(xml: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = xml.encode()
    resp.raise_for_status.return_value = None
    return resp


def test_iprogrammatori_empty_results() -> None:
    xml = "<?xml version='1.0'?><jobs></jobs>"
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_xml_response(xml)
        c = IProgrammatoriConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []


def test_iprogrammatori_yields_dicts() -> None:
    xml = """<?xml version="1.0"?>
<jobs>
  <job>
    <title>Sviluppatore Python</title>
    <company>TechCorp</company>
    <content>Descrizione del lavoro con Python e Django.</content>
    <url>https://iprogrammatori.it/job/1</url>
    <city>Milano</city>
    <date>15/05/2026</date>
  </job>
</jobs>"""
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_xml_response(xml)
        c = IProgrammatoriConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Sviluppatore Python"
    assert j["company_name"] == "TechCorp"
    assert j["url"] == "https://iprogrammatori.it/job/1"
    assert j["source"] == "IProgrammatori"


def test_iprogrammatori_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = IProgrammatoriConnector()
        jobs = list(itertools.islice(c.fetch(), 5))
    assert jobs == []
