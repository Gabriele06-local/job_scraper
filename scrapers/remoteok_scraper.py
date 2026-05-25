import requests
import structlog
from utils.retry import safe_get
from typing import List, Dict
from datetime import datetime
from .base_scraper import BaseScraper

logger = structlog.get_logger(__name__)


class RemoteOKScraper(BaseScraper):
    """Scraper for RemoteOK API"""

    def __init__(self):
        self.api_url = "https://remoteok.com/api"

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        # RemoteOK is primarily English/WorldWide.
        # We can filter by tag (keyword).

        # RemoteOK API doesn't support search params in the URL like ?search=keyword in a standard way for the JSON API,
        # but supports filtering by tag if getting specific feeds, but the main /api returns all recent jobs.
        # We will fetch recent jobs and filter client-side for the keyword.

        try:
            response = safe_get(self.api_url, timeout=10)
            response.raise_for_status()
            data = response.json()

            jobs = []
            # First query param is metadata, skip it
            if data and isinstance(data, list) and len(data) > 0:
                if "legal" in data[0]:
                    data = data[1:]

            for item in data:
                # Filter by keyword in title, description or tags
                title = item.get("position", "")
                description = item.get("description", "")
                tags = item.get("tags", [])

                # Check if keyword matches
                search_text = (title + " " + " ".join(tags)).lower()
                if keyword.lower() not in search_text:
                    continue

                # Convert date
                pub_date = item.get("date")  # Usually '2023-10-27T...' or generic string
                # RemoteOK date format in API is date string like "2023-10-27T15:24:02+00:00"
                # But sometimes it might be missing.

                jobs.append(
                    {
                        "title": title,
                        "company_name": item.get("company"),
                        "description": self.clean_description(description),
                        "url": item.get("url") or item.get("apply_url"),
                        "location_raw": item.get("location"),
                        "source": "RemoteOK",
                        "original_language": "en",
                        "published_at": pub_date.split("T")[0] if pub_date else None,
                        "remote": True,
                    }
                )

            return jobs

        except Exception as e:
            logger.error("remoteok.fetch_error", error=str(e))
            return []
