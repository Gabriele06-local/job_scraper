"""
Unit tests for ReteInformaticaLavoroScraper.

All HTTP calls are mocked so no network is required.
"""
import sys
import os
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.reteinformaticalavoro_scraper import ReteInformaticaLavoroScraper

# ---------------------------------------------------------------------------
# HTML Fixtures
# ---------------------------------------------------------------------------

LISTING_HTML = """
<html><body>
  <div class="job-card">
    <h2><a href="/lavoro/59239/support-operation-bari">Support Operation</a></h2>
    <a href="/azienda/1659-silicondev-spa">SILICONDEV SPA</a>
    <a href="/lavoro/59239/support-operation-bari" class="detail-link">Vedi i dettagli</a>
  </div>
  <div class="job-card">
    <h2><a href="/lavoro/59241/qa-engineer-milano">QA Engineer</a></h2>
    <a href="/azienda/444-gecal">Gruppo Gecal</a>
    <a href="/lavoro/59241/qa-engineer-milano" class="detail-link">Vedi i dettagli</a>
  </div>
</body></html>
"""

LISTING_HTML_EMPTY = """
<html><body>
  <div class="no-results">Nessuna offerta trovata.</div>
</body></html>
"""

DETAIL_HTML_WITH_RAL = """
<html><body>
  <header><nav>Menu</nav></header>
  <img src="/images/company/63469dcf7140b_300_300.png" alt="Logo SILICONDEV SPA" />
  <h1>Support Operation</h1>
  <a href="/azienda/1659-silicondev-spa">SILICONDEV SPA</a>
  <a href="/offerte-di-lavoro/bari">Bari (BA)</a>
  <div class="job-meta">
    Tipo di Offerta: Full time
    Tipo Contratto: Da definire
    Seniority: Middle
    RAL: Da €23000 A €27000
    Esperienza: da 1 a 3 anni
    Tag:
    <a href="/offerte-di-lavoro/estero/sql">Sql</a>
  </div>
  <span>Pubblicato 03-03-2026</span>
  <div class="job-body">
    <p>Silicondev, azienda leader nell'area IT e Software Development.</p>
    <p>Sono richieste dimestichezze informatiche, conoscenza del linguaggio SQL.</p>
    <p>Sede di Lavoro: Bari. Modalita di lavoro: Ibrida.</p>
  </div>
  <div>Condividi</div>
  <div class="altre-offerte">
    <h3>Altre offerte...</h3>
  </div>
  <footer>Footer content</footer>
</body></html>
"""

DETAIL_HTML_NO_RAL = """
<html><body>
  <h1>Java Developer</h1>
  <a href="/azienda/100-acme">Acme Srl</a>
  <a href="/offerte-di-lavoro/milano">Milano (MI)</a>
  <div class="job-meta">
    Tipo di Offerta: Full time
    Remote working: Totale
    Tag:
    <a href="/offerte-di-lavoro/estero/java">Java</a>
    <a href="/offerte-di-lavoro/estero/spring-boot">Spring Boot</a>
  </div>
  <span>Pubblicato 03-03-2026</span>
  <div class="job-body">
    <p>Cerchiamo un Java Developer senior con esperienza in Spring Boot.</p>
    <p>Offriamo contratto indeterminato e smart working totale.</p>
  </div>
  <div>Condividi</div>
  <footer>Footer</footer>
</body></html>
"""

DETAIL_HTML_NO_TITLE = """
<html><body>
  <div>No h1 here</div>
</body></html>
"""

DETAIL_HTML_NO_DESC = """
<html><body>
  <h1>Orphan Job</h1>
  <a href="/azienda/1-test">Test Srl</a>
  <span>Pubblicato 01-01-2026</span>
</body></html>
"""

DETAIL_HTML_SINGLE_RAL = """
<html><body>
  <h1>DevOps Engineer</h1>
  <a href="/azienda/200-beta">Beta Tech</a>
  <a href="/offerte-di-lavoro/roma">Roma (RM)</a>
  <div>RAL: €45000</div>
  <span>Pubblicato 01-01-2026</span>
  <div>
    <p>Descrizione del ruolo DevOps.</p>
  </div>
  <div>Condividi</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(html: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = html.encode("utf-8")
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseListingPage:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    def test_extracts_job_links(self):
        soup = BeautifulSoup(LISTING_HTML, "html.parser")
        links = self.scraper._parse_listing_page(soup)
        assert len(links) == 2
        assert "https://reteinformaticalavoro.it/lavoro/59239/support-operation-bari" in links
        assert "https://reteinformaticalavoro.it/lavoro/59241/qa-engineer-milano" in links

    def test_no_duplicates_in_links(self):
        # LISTING_HTML has two <a> tags pointing to the same /lavoro/59239/… URL
        soup = BeautifulSoup(LISTING_HTML, "html.parser")
        links = self.scraper._parse_listing_page(soup)
        assert len(links) == len(set(links))

    def test_empty_listing_returns_empty(self):
        soup = BeautifulSoup(LISTING_HTML_EMPTY, "html.parser")
        links = self.scraper._parse_listing_page(soup)
        assert links == []


class TestExtractSalary:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    def test_range_ral(self):
        text = "RAL: Da €23000 A €27000"
        sal_min, sal_max = self.scraper._extract_salary(text)
        assert sal_min == 23000.0
        assert sal_max == 27000.0

    def test_range_ral_dot_thousands(self):
        text = "RAL: Da €23.000 A €27.000"
        sal_min, sal_max = self.scraper._extract_salary(text)
        assert sal_min == 23000.0
        assert sal_max == 27000.0

    def test_range_ral_dash_separator(self):
        text = "RAL: €30000 - €40000"
        sal_min, sal_max = self.scraper._extract_salary(text)
        assert sal_min == 30000.0
        assert sal_max == 40000.0

    def test_single_ral(self):
        text = "RAL: €45000"
        sal_min, sal_max = self.scraper._extract_salary(text)
        assert sal_min == 45000.0
        assert sal_max == 45000.0

    def test_no_ral(self):
        text = "Tipo di Offerta: Full time. Nessun dato sulla RAL."
        sal_min, sal_max = self.scraper._extract_salary(text)
        assert sal_min is None
        assert sal_max is None


class TestExtractDate:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    def test_valid_date(self):
        text = "Pubblicato 03-03-2026"
        result = self.scraper._extract_date(text)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 3

    def test_no_date(self):
        result = self.scraper._extract_date("No date here")
        assert result is None


class TestExtractRemote:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    def test_totale_is_remote(self):
        assert self.scraper._extract_remote("Remote working: Totale") is True

    def test_parziale_is_remote(self):
        assert self.scraper._extract_remote("Remote working: Parziale") is True

    def test_absent_is_not_remote(self):
        assert self.scraper._extract_remote("Tipo di Offerta: Full time") is False


class TestExtractSkillTags:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    def test_extracts_tags(self):
        soup = BeautifulSoup(DETAIL_HTML_WITH_RAL, "html.parser")
        tags = self.scraper._extract_skill_tags(soup)
        assert "Sql" in tags

    def test_multiple_tags(self):
        soup = BeautifulSoup(DETAIL_HTML_NO_RAL, "html.parser")
        tags = self.scraper._extract_skill_tags(soup)
        assert "Java" in tags
        assert "Spring Boot" in tags

    def test_no_duplicates(self):
        soup = BeautifulSoup(DETAIL_HTML_NO_RAL, "html.parser")
        tags = self.scraper._extract_skill_tags(soup)
        assert len(tags) == len(set(tags))


class TestParseDetailPage:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    def test_full_job_with_ral(self):
        soup = BeautifulSoup(DETAIL_HTML_WITH_RAL, "html.parser")
        job = self.scraper._parse_detail_page(soup, "https://reteinformaticalavoro.it/lavoro/59239/support-operation-bari")

        assert job is not None
        assert job["title"] == "Support Operation"
        assert job["company"]["name"] == "SILICONDEV SPA"
        assert job["company"]["logo"] is not None
        assert "reteinformaticalavoro.it/images/company/" in job["company"]["logo"]
        assert job["location_raw"] == "Bari (BA)"
        assert job["remote"] is False
        assert job["salary_min"] == 23000.0
        assert job["salary_max"] == 27000.0
        assert isinstance(job["published_at"], datetime)
        assert job["published_at"].day == 3
        assert job["source"] == "ReteinformaticaLavoro"
        assert job["original_language"] == "it"
        assert "Sql" in job["description"]
        assert len(job["description"]) > 100

    def test_job_remote_true(self):
        soup = BeautifulSoup(DETAIL_HTML_NO_RAL, "html.parser")
        job = self.scraper._parse_detail_page(soup, "https://reteinformaticalavoro.it/lavoro/99/java-developer-milano")

        assert job is not None
        assert job["remote"] is True
        assert job["salary_min"] is None
        assert job["salary_max"] is None
        assert "Java" in job["description"]
        assert "Spring Boot" in job["description"]

    def test_job_single_ral(self):
        soup = BeautifulSoup(DETAIL_HTML_SINGLE_RAL, "html.parser")
        job = self.scraper._parse_detail_page(soup, "https://reteinformaticalavoro.it/lavoro/200/devops-roma")

        assert job is not None
        assert job["salary_min"] == 45000.0
        assert job["salary_max"] == 45000.0

    def test_missing_title_returns_none(self):
        soup = BeautifulSoup(DETAIL_HTML_NO_TITLE, "html.parser")
        result = self.scraper._parse_detail_page(soup, "https://reteinformaticalavoro.it/lavoro/1/x")
        assert result is None

    def test_empty_description_returns_none(self):
        soup = BeautifulSoup(DETAIL_HTML_NO_DESC, "html.parser")
        result = self.scraper._parse_detail_page(soup, "https://reteinformaticalavoro.it/lavoro/2/y")
        assert result is None

    def test_description_excludes_footer_and_condividi(self):
        soup = BeautifulSoup(DETAIL_HTML_WITH_RAL, "html.parser")
        job = self.scraper._parse_detail_page(soup, "https://reteinformaticalavoro.it/lavoro/59239/support-operation-bari")
        assert job is not None
        assert "Footer content" not in job["description"]
        assert "Condividi" not in job["description"]
        assert "Altre offerte" not in job["description"]


class TestFetchDetail:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper()

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    def test_http_error_returns_none(self, mock_get):
        mock_get.return_value = _make_mock_response("", status_code=404)
        result = self.scraper._fetch_detail("https://reteinformaticalavoro.it/lavoro/99/not-found")
        assert result is None

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    def test_connection_error_returns_none(self, mock_get):
        from requests import ConnectionError as ReqConnError
        mock_get.side_effect = ReqConnError("Connection refused")
        result = self.scraper._fetch_detail("https://reteinformaticalavoro.it/lavoro/99/not-found")
        assert result is None

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    @patch("scrapers.reteinformaticalavoro_scraper.time.sleep")
    def test_successful_fetch_returns_job(self, mock_sleep, mock_get):
        mock_get.return_value = _make_mock_response(DETAIL_HTML_WITH_RAL)
        job = self.scraper._fetch_detail("https://reteinformaticalavoro.it/lavoro/59239/support-operation-bari")
        assert job is not None
        assert job["title"] == "Support Operation"
        assert job["salary_min"] == 23000.0


class TestCollectListingLinks:
    def setup_method(self):
        self.scraper = ReteInformaticaLavoroScraper(max_pages=2)

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    @patch("scrapers.reteinformaticalavoro_scraper.time.sleep")
    def test_stops_on_empty_page(self, mock_sleep, mock_get):
        """If page 1 has results but page 2 is empty, stop paginating."""
        mock_get.side_effect = [
            _make_mock_response(LISTING_HTML),
            _make_mock_response(LISTING_HTML_EMPTY),
        ]
        links = self.scraper._collect_listing_links("java")
        assert len(links) == 2  # only from page 1

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    @patch("scrapers.reteinformaticalavoro_scraper.time.sleep")
    def test_http_error_stops_pagination(self, mock_sleep, mock_get):
        from requests import HTTPError
        mock_get.side_effect = HTTPError("503 Service Unavailable")
        links = self.scraper._collect_listing_links("python")
        assert links == []


class TestScrapeIntegration:
    """End-to-end integration test with all HTTP mocked."""

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    @patch("scrapers.reteinformaticalavoro_scraper.time.sleep")
    def test_full_scrape_flow(self, mock_sleep, mock_get):
        scraper = ReteInformaticaLavoroScraper(max_pages=1)

        mock_get.side_effect = [
            _make_mock_response(LISTING_HTML),         # listing page 1
            _make_mock_response(DETAIL_HTML_WITH_RAL), # detail job 1
            _make_mock_response(DETAIL_HTML_NO_RAL),   # detail job 2
        ]

        jobs = asyncio.run(scraper.scrape("sql", "it"))

        assert len(jobs) == 2
        # First job has RAL
        job_ral = next(j for j in jobs if j["salary_min"] is not None)
        assert job_ral["salary_min"] == 23000.0
        assert job_ral["salary_max"] == 27000.0
        # Second job has no RAL
        job_no_ral = next(j for j in jobs if j["salary_min"] is None)
        assert job_no_ral["salary_min"] is None

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    @patch("scrapers.reteinformaticalavoro_scraper.time.sleep")
    def test_scrape_other_lang_works(self, mock_sleep, mock_get):
        """The scraper works for any lang — same IT site, tech keywords are universal."""
        scraper = ReteInformaticaLavoroScraper(max_pages=1)
        mock_get.side_effect = [
            _make_mock_response(LISTING_HTML),
            _make_mock_response(DETAIL_HTML_WITH_RAL),
            _make_mock_response(DETAIL_HTML_NO_RAL),
        ]

        for lang in ["en", "es", "fr", "de"]:
            mock_get.side_effect = [
                _make_mock_response(LISTING_HTML),
                _make_mock_response(DETAIL_HTML_WITH_RAL),
                _make_mock_response(DETAIL_HTML_NO_RAL),
            ]
            jobs = asyncio.run(scraper.scrape("java", lang))
            assert len(jobs) == 2
            assert all(j["original_language"] == "it" for j in jobs)

    @patch("scrapers.reteinformaticalavoro_scraper.requests.get")
    @patch("scrapers.reteinformaticalavoro_scraper.time.sleep")
    def test_detail_error_does_not_abort_other_jobs(self, mock_sleep, mock_get):
        """If one detail page fails, the others are still processed."""
        from requests import HTTPError
        scraper = ReteInformaticaLavoroScraper(max_pages=1)

        mock_get.side_effect = [
            _make_mock_response(LISTING_HTML),          # listing: 2 links
            _make_mock_response("", status_code=500),   # detail 1 fails
            _make_mock_response(DETAIL_HTML_WITH_RAL),  # detail 2 succeeds
        ]

        jobs = asyncio.run(scraper.scrape("devops", "it"))
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Support Operation"
