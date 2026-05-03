"""Unit tests for Personio ATS connector (XML parsing)."""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

from connectors.personio import PersonioConnector

_VALID_XML = """<?xml version='1.0' encoding='UTF-8'?>
<workzag-jobs>
  <position>
    <id>1001</id>
    <name>Python Backend Engineer</name>
    <department>Engineering</department>
    <office>Berlin</office>
    <job-descriptions>
      <job-description>
        <name>About the role</name>
        <value>Build Python services at scale.</value>
      </job-description>
    </job-descriptions>
    <employment-type>permanent</employment-type>
    <schedule>full-time</schedule>
    <recruitingCategory>Software Engineering</recruitingCategory>
    <applicationUrl>https://myco.jobs.personio.de/job/1001</applicationUrl>
  </position>
</workzag-jobs>
"""

_EMPTY_XML = "<?xml version='1.0'?><workzag-jobs></workzag-jobs>"


def _mock_response(body: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.content = body.encode()
    resp.raise_for_status.return_value = None
    return resp


def test_personio_empty_xml() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(_EMPTY_XML)
        c = PersonioConnector()
        c._companies = [{"name": "MyCo", "slug": "myco"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_personio_parses_job() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(_VALID_XML)
        c = PersonioConnector()
        c._companies = [{"name": "MyCo", "slug": "myco"}]
        jobs = list(itertools.islice(c.fetch(), 5))
    assert len(jobs) == 1
    assert isinstance(jobs[0], dict)
    assert jobs[0]["title"] == "Python Backend Engineer"


def test_personio_no_crash_on_http_error() -> None:
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("DNS failure")
        c = PersonioConnector()
        c._companies = [{"name": "MyCo", "slug": "myco"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)


def test_personio_no_crash_on_bad_xml() -> None:
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response("not xml at all")
        c = PersonioConnector()
        c._companies = [{"name": "MyCo", "slug": "myco"}]
        jobs = list(c.fetch())
    assert isinstance(jobs, list)
