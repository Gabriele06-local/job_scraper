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


