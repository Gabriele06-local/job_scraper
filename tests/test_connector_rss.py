"""Unit tests for RSS connector -- company extraction from feed metadata."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from connectors.rss import RSSConnector


def _xml_response(xml: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = xml.encode()
    resp.raise_for_status.return_value = None
    return resp


_FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel><title>Jobs</title>
{}
</channel></rss>"""


def test_rss_empty_feed() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(
            _FEED_TEMPLATE.format("")
        )
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(c.fetch())
    assert jobs == []


def test_rss_extracts_company_from_dc_creator() -> None:
    xml = _FEED_TEMPLATE.format("""
    <item>
        <title>Senior Engineer</title>
        <description>Job description text long enough for testing.</description>
        <link>https://example.com/job/1</link>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
        <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Acme Corp</dc:creator>
    </item>""")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert jobs[0]["company_name"] == "Acme Corp"


def test_rss_extracts_company_from_author_tag() -> None:
    xml = _FEED_TEMPLATE.format("""
    <item>
        <title>Engineer</title>
        <description>Job description text long enough for testing.</description>
        <link>https://example.com/job/2</link>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
        <author>TechCo Inc</author>
    </item>""")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert jobs[0]["company_name"] == "TechCo Inc"


def test_rss_falls_back_to_title_prefix() -> None:
    xml = _FEED_TEMPLATE.format("""
    <item>
        <title>Acme Corp – Senior Engineer</title>
        <description>Job description text long enough for testing.</description>
        <link>https://example.com/job/3</link>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
    </item>""")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert jobs[0]["company_name"] == "Acme Corp"


def test_rss_unknown_company_when_no_metadata() -> None:
    xml = _FEED_TEMPLATE.format("""
    <item>
        <title>Python Developer</title>
        <description>Job description text long enough for testing.</description>
        <link>https://example.com/job/4</link>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
    </item>""")
    with patch("requests.get") as mock_get:
        mock_get.return_value = _xml_response(xml)
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(c.fetch())
    assert len(jobs) >= 1
    assert jobs[0]["company_name"] == "Unknown"


def test_rss_no_crash_on_text_parse_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        c = RSSConnector(
            rss_urls={"en": ["https://example.com/feed.rss"]},
            languages=["en"],
        )
        jobs = list(c.fetch())
    assert jobs == []
