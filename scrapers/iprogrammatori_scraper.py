import requests
import logging
from bs4 import BeautifulSoup
from typing import List, Dict
from datetime import datetime
from .base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class IProgrammatoriScraper(BaseScraper):
    """Scraper for IProgrammatori.it RSS/XML feed"""

    def __init__(self):
        self.feed_url = "https://www.iprogrammatori.it/rss/offerte-lavoro-crawler.xml"

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        # IProgrammatori is mainly for Italian market
        if lang not in ["it"]:
            return []

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            response = requests.get(self.feed_url, headers=headers, timeout=10)
            response.raise_for_status()

            # The feed is XML
            soup = BeautifulSoup(response.content, "xml")
            jobs_xml = soup.find_all("job")

            jobs = []
            for item in jobs_xml:
                title = item.find("title").text if item.find("title") else ""
                description = item.find("content").text if item.find("content") else ""

                # Check keyword relevance (client-side filtering)
                # Combine title and description for search
                full_text = (title + " " + description).lower()
                if keyword.lower() not in full_text:
                    continue

                # Parse date: 14/01/2026
                pub_date = None
                date_str = item.find("date").text if item.find("date") else ""
                if date_str:
                    try:
                        pub_date = datetime.strptime(date_str.strip(), "%d/%m/%Y")
                    except Exception:
                        pass

                link = item.find("url").text if item.find("url") else ""
                if not link:
                    continue

                jobs.append(
                    {
                        "title": title,
                        "company": {
                            "name": (
                                item.find("company").text if item.find("company") else "Unknown"
                            ),
                            "logo": None,
                        },
                        "description": self.clean_description(description),
                        "link": link,
                        "location_raw": (item.find("city").text if item.find("city") else ""),
                        "source": "IProgrammatori",
                        "original_language": "it",
                        "published_at": pub_date,
                        "remote": False,  # Feed doesn't explicitly state remote usually, AI will refine
                    }
                )

            return jobs

        except Exception as e:
            logger.error(f"Error scraping IProgrammatori: {e}")
            return []
