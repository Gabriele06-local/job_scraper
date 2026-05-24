import pytest
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import JobScraperOrchestrator


@pytest.mark.asyncio
async def test_scraper_orchestrator_flow():
    # Setup mocks
    mock_db = Mock()
    mock_db.upsert_company.return_value = "company_123"
    mock_db.upsert_seniority.return_value = "seniority_123"
    mock_db.insert_job.return_value = True

    mock_categorizer = AsyncMock()
    mock_categorizer.categorize_job.return_value = {
        "skills": ["Python", "Django"],
        "seniority": "Senior",
        "formatted_address": "Rome, Italy",
        "city": "Rome",
    }

    mock_geocoder = Mock()
    mock_geocoder.get_coordinates.return_value = {
        "lat": 41.9,
        "lng": 12.5,
        "formatted_address": "Rome, Italy",
    }

    mock_deduplicator = Mock()
    mock_deduplicator.is_duplicate.return_value = False

    mock_desc_fetcher = AsyncMock()
    mock_desc_fetcher.fetch.return_value = "Full job description content..."

    # Create orchestrator with properly patched dependencies
    with (
        patch("main.MongoDBClient", return_value=mock_db),
        patch("main.JobCategorizer", return_value=mock_categorizer),
        patch("main.Geocoder", return_value=mock_geocoder),
        patch("main.JobDeduplicator", return_value=mock_deduplicator),
        patch("main.DescriptionFetcher", return_value=mock_desc_fetcher),
    ):
        orchestrator = JobScraperOrchestrator(languages=["en"], limit_per_language=5)

        # Mock the scrapers list to have a single fake scraper
        mock_scraper = AsyncMock()
        # Return a list of fake jobs
        mock_scraper.scrape.return_value = [
            {
                "title": "Python Developer",
                "description": "Short desc",
                "link": "http://test.com/job1",
                "published_at": "today",
                "company": {"name": "Test Co"},
            }
        ]

        # Replace real scrapers with our mock
        orchestrator.scrapers = [mock_scraper]

        # Run the orchestrator
        await orchestrator.run()

        # Verifications
        assert mock_scraper.scrape.called

        # Check flow
        assert mock_deduplicator.is_duplicate.called
        assert mock_categorizer.categorize_job.called
        assert mock_geocoder.get_coordinates.called
        assert mock_db.upsert_company.called
        assert mock_db.insert_job.called

        # Check that description was fetched (because snippet was short)
        assert mock_desc_fetcher.fetch.called


@pytest.mark.asyncio
async def test_scraper_skips_duplicates():
    # Setup mocks
    mock_db = Mock()
    mock_db.insert_job.return_value = True

    # We need the INSTANCE to be the mock with the behavior
    mock_dedup_instance = Mock()
    mock_dedup_instance.is_duplicate.return_value = True  # Always duplicate

    with (
        patch("main.MongoDBClient", return_value=mock_db),
        patch("main.JobCategorizer"),
        patch("main.Geocoder"),
        patch("main.JobDeduplicator", return_value=mock_dedup_instance),
        patch("main.DescriptionFetcher"),
    ):
        orchestrator = JobScraperOrchestrator(languages=["en"])

        # Override the scrapers with a mock that behaves correctly as async
        mock_scraper = Mock()
        mock_scraper.scrape = AsyncMock(
            return_value=[
                {
                    "title": "Python Duplicate Job",
                    "link": "http://old.com",
                    "published_at": "today",
                    "description": "Long enough description to avoid fetching" + ("x" * 500),
                }
            ]
        )

        # Inject the mock scraper
        orchestrator.scrapers = [mock_scraper]

        await orchestrator.run()

        # Should have checked duplicate
        assert mock_dedup_instance.is_duplicate.called

        # Should NOT have inserted because is_duplicate returned True
        mock_db.insert_job.assert_not_called()
