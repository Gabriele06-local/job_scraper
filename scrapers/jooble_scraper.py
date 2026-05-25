import requests
import structlog
from utils.retry import safe_post
import os
from datetime import datetime, timedelta
from typing import List, Dict
from urllib.parse import parse_qs, urlparse

from .base_scraper import BaseScraper

logger = structlog.get_logger(__name__)


def _is_closed_jooble_job(url: str) -> bool:
    """Return True if Jooble URL signals a closed/unavailable listing."""
    try:
        params = parse_qs(urlparse(url).query)
        return params.get("closedJob", [""])[0].lower() == "true"
    except Exception:
        return False


class JoobleScraper(BaseScraper):
    """Scraper for Jooble API (Powerful aggregator)"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("JOOBLE_API_KEY")
        self.base_url = "https://jooble.org/api/"

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        if not self.api_key:
            # logger.warning("Jooble API Key missing, skipping.")
            return []

        # Map languages to Jooble domains
        # Jooble uses specific subdomains for each country
        domains = {
            "it": "https://it.jooble.org/api",
            "en": "https://jooble.org/api",  # US/Global
            "es": "https://es.jooble.org/api",
            "fr": "https://fr.jooble.org/api",
            "de": "https://de.jooble.org/api",
            "uk": "https://uk.jooble.org/api",
            "pt": "https://pt.jooble.org/api",
        }

        base_url = domains.get(lang.lower(), "https://jooble.org/api")
        url = f"{base_url}/{self.api_key}"

        # Jooble API treats "language" by the regional endpoint,
        # but the JSON body can specify location.
        location = "Italy" if lang == "it" else ""  # Simplified

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        payload = {
            "keywords": keyword,
            "location": location,
            "dateFrom": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        }

        try:
            response = safe_post(url, json=payload, headers=headers, timeout=10)

            if response.status_code == 403:
                logger.error("jooble.403_forbidden", lang=lang, url=url)
                return []

            response.raise_for_status()
            data = response.json()

            jobs = []
            skipped_closed = 0
            for item in data.get("jobs", []):
                link = item.get("link") or ""
                if _is_closed_jooble_job(link):
                    skipped_closed += 1
                    continue
                jobs.append(
                    {
                        "title": item.get("title"),
                        "company_name": item.get("company") or "Unknown",
                        "description": self.clean_description(item.get("snippet")),
                        "url": link,
                        "location_raw": item.get("location"),
                        "source": f"Jooble ({item.get('source', 'Unknown')})",
                        "original_language": lang,
                        "published_at": item.get("updated"),
                    }
                )
            if skipped_closed:
                logger.info("jooble.skipped_closed", count=skipped_closed, keyword=keyword, lang=lang)
            return jobs
        except Exception as e:
            logger.error("jooble.fetch_error", error=str(e))
            return []
