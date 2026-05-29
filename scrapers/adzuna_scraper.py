from __future__ import annotations

import requests
import structlog
from utils.retry import safe_get
from typing import List, Dict
from .base_scraper import BaseScraper

logger = structlog.get_logger(__name__)


class AdzunaScraper(BaseScraper):
    """Scraper for Adzuna API (supports multiple countries)."""

    COUNTRIES = ["gb", "us", "de", "nl", "fr", "au", "ca", "at", "be", "nz"]

    def __init__(self, app_id: str, app_key: str):
        self.app_id = app_id
        self.app_key = app_key
        self.base_url = "https://api.adzuna.com/v1/api/jobs"

    async def scrape(
        self,
        keyword: str = None,
        lang: str = "it",
        category: str = "it-jobs",
        page: int = 1,
        country: str | None = None,
    ) -> List[Dict]:
        _country = country or "it"
        url = f"{self.base_url}/{_country}/search/{page}"

        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": 50,
            "content-type": "application/json",
        }

        if keyword:
            params["what"] = keyword

        if category:
            params["category"] = category

        if not self.app_id or not self.app_key:
            logger.warning("adzuna.credentials_missing")
            return []

        try:
            response = safe_get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            jobs = []
            for item in data.get("results", []):
                jobs.append(
                    {
                        "title": item.get("title"),
                        "company_name": item.get("company", {}).get("display_name"),
                        "description": self.clean_description(item.get("description")),
                        "url": item.get("redirect_url"),
                        "location_raw": item.get("location", {}).get("display_name"),
                        "source": "Adzuna",
                        "original_language": lang,
                        "country": _country,
                        "published_at": item.get("created"),
                        "salary_min": item.get("salary_min"),
                        "salary_max": item.get("salary_max"),
                    }
                )
            return jobs
        except Exception as e:
            logger.error("adzuna.fetch_error", error=str(e))
            return []
