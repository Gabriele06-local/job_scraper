import requests
import logging
from typing import List, Dict
from datetime import datetime
from .base_scraper import BaseScraper
import time

logger = logging.getLogger(__name__)


class ArbeitnowScraper(BaseScraper):
    """Scraper for Arbeitnow API"""

    def __init__(self):
        self.api_url = "https://www.arbeitnow.com/api/job-board-api"

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://jobicy.com/",
            }
            # Retry logic for 429
            max_retries = 3
            data = {}

            for attempt in range(max_retries):
                time.sleep(5)
                try:
                    response = requests.get(self.api_url, headers=headers, timeout=10)

                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        logger.warning(
                            f"Arbeitnow rate limited (429). Waiting {retry_after} seconds..."
                        )
                        time.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == max_retries - 1:
                        raise e
                    logger.warning(f"Arbeitnow request failed: {e}. Retrying...")
            else:
                logger.error("Arbeitnow: Failed to fetch data after retries.")
                return []

            jobs = []
            for item in data.get("data", []):
                title = item.get("title", "")
                description = item.get("description", "")
                tags = item.get("tags", [])

                # Filter by keyword
                search_text = (title + " " + description + " ".join(tags)).lower()
                if keyword.lower() not in search_text:
                    continue

                # Arbeitnow dates are timestamps, e.g. 1698322633
                created_at = item.get("created_at")
                pub_date = None
                if created_at:
                    try:
                        pub_date = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d")
                    except:
                        pass

                jobs.append(
                    {
                        "title": title,
                        "company": {
                            "name": item.get("company_name"),
                            "logo": None,
                        },  # Logo URL is not always direct
                        "description": self.clean_description(description),
                        "link": item.get("url"),
                        "location_raw": item.get("location"),
                        "source": "Arbeitnow",
                        "original_language": "en",  # Mostly English/German
                        "published_at": pub_date,
                        "remote": item.get("remote", False),
                    }
                )

            return jobs

        except Exception as e:
            logger.error(f"Error scraping Arbeitnow: {e}")
            return []
