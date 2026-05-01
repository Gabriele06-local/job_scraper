import pytest
import datetime
from datetime import date
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import JobScraperOrchestrator
from utils.deduplicator import JobDeduplicator


class TestJobScraperOrchestrator:
    @pytest.fixture
    def orchestrator(self):
        with patch("main.MongoDBClient"), patch("main.JobCategorizer"), patch(
            "main.Geocoder"
        ), patch("main.JobDeduplicator"), patch("main.DescriptionFetcher"):
            return JobScraperOrchestrator(languages=["en"])

    def test_parse_date_string_formats(self, orchestrator):
        # ISO format
        dt = orchestrator.parse_date("2023-01-01T12:00:00Z")
        assert dt.year == 2023 and dt.month == 1 and dt.day == 1

        # Simple date
        dt = orchestrator.parse_date("2023-05-20")
        assert dt.year == 2023 and dt.month == 5 and dt.day == 20

        # Human readable
        dt = orchestrator.parse_date("15 Jan 2024")
        assert dt.year == 2024 and dt.month == 1 and dt.day == 15

    def test_parse_date_special_values(self, orchestrator):
        assert orchestrator.parse_date("older") is None
        assert orchestrator.parse_date(None) is None
        assert orchestrator.parse_date("") is None

    def test_parse_date_objects(self, orchestrator):
        now = datetime.datetime.now()
        assert orchestrator.parse_date(now) == now

        today = date.today()
        parsed = orchestrator.parse_date(today)
        assert parsed.year == today.year and parsed.month == today.month

    def test_is_published_today(self, orchestrator):
        # Mock today
        assert orchestrator.is_published_today("today") is True
        assert orchestrator.is_published_today("Oggi") is True

        # Current date
        now_iso = datetime.datetime.now().isoformat()
        assert orchestrator.is_published_today(now_iso) is True

        # Old date
        old_date = (datetime.datetime.now() - datetime.timedelta(days=5)).isoformat()
        assert orchestrator.is_published_today(old_date) is False


class TestJobDeduplicator:
    def test_is_duplicate_true(self):
        mock_db = Mock()
        # Simulate finding a document
        mock_db.jobs.find_one.return_value = {
            "_id": "123",
            "link": "http://example.com",
        }

        deduplicator = JobDeduplicator(mock_db)
        assert deduplicator.is_duplicate({"link": "http://example.com"}) is True
        mock_db.jobs.find_one.assert_called_with({"link": "http://example.com"})

    def test_is_duplicate_false(self):
        mock_db = Mock()
        # Simulate not finding a document
        mock_db.jobs.find_one.return_value = None

        deduplicator = JobDeduplicator(mock_db)
        assert deduplicator.is_duplicate({"link": "http://new.com"}) is False

    def test_is_duplicate_no_link(self):
        mock_db = Mock()
        deduplicator = JobDeduplicator(mock_db)
        assert deduplicator.is_duplicate({"title": "Job without link"}) is False


class TestLinkedInScraper:
    """Unit tests for LinkedIn scraper."""

    @pytest.fixture
    def scraper(self):
        from scrapers.linkedin_scraper import LinkedInScraper

        return LinkedInScraper()

    def test_init(self, scraper):
        assert scraper.max_results == 25
        assert scraper.fetch_details is False

    def test_is_remote(self, scraper):
        """Test remote job detection logic."""
        # Test title detection
        assert scraper._is_remote("Senior Python Developer - Remote", "Rome") is True
        assert scraper._is_remote("Remote Software Engineer", "") is True
        assert scraper._is_remote("Java Developer", "Remote") is True

        # Test location detection
        assert scraper._is_remote("Dev", "Télétravail") is True
        assert scraper._is_remote("Dev", "Home Office") is True

        # Test negative cases
        assert scraper._is_remote("Python Developer", "Rome, Italy") is False

    def test_parse_job_card_success(self, scraper):
        """Test parsing a valid job card HTML."""
        from bs4 import BeautifulSoup

        html = """
        <div class="base-card" data-entity-urn="urn:li:jobPosting:123456789">
            <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/123456789/?trackingId=abc"></a>
            <div class="base-search-card__info">
                <h3 class="base-search-card__title">Senior Developer</h3>
                <h4 class="base-search-card__subtitle">Tech Corp</h4>
                <div class="base-search-card__metadata">
                    <span class="job-search-card__location">Rome, Italy</span>
                    <time class="job-search-card__listdate" datetime="2024-01-01">1 day ago</time>
                </div>
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        card = soup.find("div", class_="base-card")

        result = scraper._parse_job_card(card, "it")

        assert result is not None
        assert result["title"] == "Senior Developer"
        assert result["company"]["name"] == "Tech Corp"
        assert result["location_raw"] == "Rome, Italy"
        assert result["external_id"] == "123456789"
        # The scraper splits on '?' to clean the link
        assert result["link"] == "https://www.linkedin.com/jobs/view/123456789/"
        assert result["original_language"] == "it"
        assert result["source"] == "LinkedIn"

    def test_parse_job_card_missing_info(self, scraper):
        """Test parsing a job card with missing critical info."""
        from bs4 import BeautifulSoup

        # Missing title and link
        html = """
        <div class="base-card">
            <div class="base-search-card__info">
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        card = soup.find("div", class_="base-card")

        result = scraper._parse_job_card(card, "it")
        assert result is None
