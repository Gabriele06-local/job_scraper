import requests
import logging
from bs4 import BeautifulSoup
from typing import List, Dict
from .base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class RSSScraper(BaseScraper):
    """Generic RSS Scraper"""

    def __init__(self, rss_urls: Dict[str, List[str]]):
        """rss_urls: dictionary mapping language to list of RSS urls"""
        self.rss_urls = rss_urls

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        urls = self.rss_urls.get(lang, [])
        all_jobs = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        for url in urls:
            try:
                # Support keyword injection in RSS URL
                current_url = url.format(keyword=keyword) if "{keyword}" in url else url
                response = requests.get(current_url, headers=headers, timeout=10)
                soup = BeautifulSoup(response.content, "xml")
                items = soup.find_all("item")

                for item in items:
                    title = item.find("title").text if item.find("title") else ""
                    if keyword.lower() not in title.lower():
                        continue

                    all_jobs.append(
                        {
                            "title": title,
                            "company": {
                                "name": "Unknown"
                            },  # RSS often lacks company in standard fields
                            "description": self.clean_description(
                                item.find("description").text if item.find("description") else ""
                            ),
                            "link": item.find("link").text if item.find("link") else "",
                            "source": "RSS Feed",
                            "original_language": lang,
                            "published_at": (
                                item.find("pubDate").text if item.find("pubDate") else None
                            ),
                        }
                    )
            except Exception as e:
                logger.error(f"Error scraping RSS {url}: {e}")

        return all_jobs
