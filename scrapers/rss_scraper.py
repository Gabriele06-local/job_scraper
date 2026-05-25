import re
import requests
import structlog
from utils.retry import safe_get
from bs4 import BeautifulSoup
from typing import List, Dict
from .base_scraper import BaseScraper

logger = structlog.get_logger(__name__)


def _extract_company(item: BeautifulSoup, title: str) -> str:
    """Extract company name from RSS item metadata or title parsing.

    Tries in order: dc:creator, author, title pattern, fallback.
    """
    creator_tag = item.find("dc:creator")
    if creator_tag and creator_tag.text.strip():
        return creator_tag.text.strip()

    author_tag = item.find("author")
    if author_tag and author_tag.text.strip():
        return author_tag.text.strip()

    media_credit = item.find("media:credit")
    if media_credit and media_credit.text.strip():
        return media_credit.text.strip()

    m = re.match(r"^(.*?)\s*[–—-]\s*", title)
    if m:
        candidate = m.group(1).strip()
        if candidate and candidate.lower() not in ("remote", "hiring", "new", "job"):
            return candidate

    m = re.match(r"^(.*?)\s*[:|]\s*", title)
    if m:
        candidate = m.group(1).strip()
        if candidate and candidate.lower() not in ("remote", "hiring", "new", "job"):
            return candidate

    return "Unknown"


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
                current_url = url.format(keyword=keyword) if "{keyword}" in url else url
                response = safe_get(current_url, headers=headers, timeout=10)
                soup = BeautifulSoup(response.content, "xml")
                items = soup.find_all("item")

                for item in items:
                    title = item.find("title").text if item.find("title") else ""
                    if keyword.lower() not in title.lower():
                        continue

                    company_name = _extract_company(item, title)

                    all_jobs.append(
                        {
                            "title": title,
                            "company": {"name": company_name},
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
                logger.error("rss.fetch_error", url=url, error=str(e))

        return all_jobs
