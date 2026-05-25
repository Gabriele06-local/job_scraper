import requests
import structlog
from utils.retry import safe_get
from typing import List, Dict
from .base_scraper import BaseScraper

logger = structlog.get_logger(__name__)


class JobicyScraper(BaseScraper):
    """
    Scraper for Jobicy JSON API
    API Docs: https://jobicy.com/jobs-rss-feed
    Endpoint: https://jobicy.com/api/v2/remote-jobs
    """

    BASE_URL = "https://jobicy.com/api/v2/remote-jobs"

    def __init__(self):
        pass

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        # Jobicy API supports filtering by count, tag, geo, industry, types
        # We'll fetch the latest 50 remote jobs and client-side filter by keyword
        # because the API 'tag' filter is specific (e.g. 'dev', 'python') and 'keyword' isn't a direct param.
        # Alternatively, we can use their 'tag' param if keyword matches a known tag, but general search is safer client-side.

        params = {
            "count": 50,
            "geo": "",  # or 'usa', 'uk', etc. but we want global remote ('')
            "industry": "engineering",  # focuses on tech
        }

        all_jobs = []
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://jobicy.com/",
            }

            response = safe_get(self.BASE_URL, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                logger.warning("jobicy.api_failure", message=data.get("message"))
                return []

            jobs_list = data.get("jobs", [])

            for item in jobs_list:
                title = item.get("jobTitle", "")

                # Basic keyword filtering
                if (
                    keyword.lower() not in title.lower()
                    and keyword.lower() not in item.get("jobDescription", "").lower()
                ):
                    continue

                all_jobs.append(
                    {
                        "title": title,
                        "company": {
                            "name": item.get("companyName", "Unknown"),
                            "logo_url": item.get("companyLogo"),
                        },
                        "description": self.clean_description(item.get("jobDescription", "")),
                        "link": item.get("url", ""),
                        "source": "Jobicy",
                        "original_language": lang,
                        "published_at": item.get("pubDate"),  # Usually YYYY-MM-DD HH:MM:SS
                        "location_raw": item.get("jobGeo"),
                        "employment_type": item.get("jobType"),  # e.g. full-time
                        "salary_min": item.get("annualSalaryMin"),
                        "salary_max": item.get("annualSalaryMax"),
                        "remote": True,  # Jobicy is remote-first
                    }
                )

        except Exception as e:
            logger.error("jobicy.fetch_error", error=str(e))

        return all_jobs
