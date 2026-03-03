import warnings
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import logging
from datetime import datetime
from typing import List, Dict
from .base_scraper import BaseScraper

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

class JobisJobScraper(BaseScraper):
    """Scraper for JobisJob (backup scraping)"""
    
    BASE_URLS = {
        "it": "https://www.jobisjob.it",
        "es": "https://www.jobisjob.es",
        "fr": "https://www.jobisjob.fr",
        "de": "https://www.jobisjob.de",
        "en": "https://www.jobisjob.co.uk"
    }

    SEARCH_PATHS = {
        "it": "{}/lavoro",
        "es": "{}/trabajo",
        "fr": "{}/emploi",
        "de": "{}/arbeit",
        "en": "{}/jobs"
    }

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        base_url = self.BASE_URLS.get(lang, self.BASE_URLS["en"])
        path_template = self.SEARCH_PATHS.get(lang, "{}/jobs")
        # Ensure keyword is URL safe, simple replacement for now
        safe_keyword = keyword.replace(" ", "-")
        url = f"{base_url}/{path_template.format(safe_keyword)}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            job_cards = soup.select('div.offer')
            jobs = []
            
            for card in job_cards:
                title_elem = card.select_one('strong.title a')
                company_elem = card.select_one('.company')
                # Date is often in a span with class 'date' inside a p with class 'from'
                date_elem = card.select_one('.date')
                
                if title_elem and title_elem.get('href'):
                    date_text = date_elem.text.strip().lower() if date_elem else ""
                    # Basic normalization for "today" in different languages
                    is_today = any(word in date_text for word in ['oggi', 'today', 'hoy', 'aujourd', 'heute', 'just now', 'ora', 'ieri'])
                    
                    # Extract company name (remove location if dash separated)
                    company_name = "Unknown"
                    if company_elem:
                        # Often "Company - Location"
                        full_text = company_elem.get_text(strip=True)
                        if " - " in full_text:
                            company_name = full_text.split(" - ")[0]
                        else:
                            company_name = full_text

                    link = title_elem['href']
                    if not link.startswith('http'):
                         link = base_url + link if link.startswith('/') else f"{base_url}/{link}"

                    jobs.append({
                        "title": title_elem.text.strip(),
                        "company": {"name": company_name},
                        "link": link,
                        "description": "Scraped from JobisJob. Full details at link.",
                        "source": "JobisJob",
                        "original_language": lang,
                        "published_at": datetime.now().strftime('%Y-%m-%d') if is_today else None
                    })
            return jobs
        except Exception as e:
            logger.error(f"Error scraping JobisJob ({lang}): {e}")
            return []
