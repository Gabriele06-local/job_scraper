"""
LinkedIn Jobs Scraper using LinkedIn's public guest API.

This scraper uses LinkedIn's public jobs API endpoint which doesn't require
authentication or cookies. It returns HTML which is parsed using BeautifulSoup.

No external API services or subscriptions required - 100% free.
"""

import requests
import logging
import time
import re
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from .base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class LinkedInScraper(BaseScraper):
    """Scraper for LinkedIn Jobs using public guest API (no auth required)."""

    BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    JOB_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    # Map language codes to LinkedIn location strings
    LOCATION_MAP = {
        "en": "United States",
        "it": "Italy",
        "es": "Spain",
        "fr": "France",
        "de": "Germany",
        "gb": "United Kingdom",
    }

    def __init__(self, max_results: int = 25, fetch_details: bool = False):
        """
        Initialize LinkedIn scraper.

        Args:
            max_results: Maximum number of results per search (default 25)
            fetch_details: Whether to fetch full job details (slower, default False)
        """
        self.max_results = max_results
        self.fetch_details = fetch_details
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        """
        Scrape LinkedIn jobs for given keyword and language.

        Args:
            keyword: Search keyword (job title, skill, etc.)
            lang: Language/country code (en, it, es, fr, de)

        Returns:
            List of job dictionaries in internal format
        """
        location = self.LOCATION_MAP.get(lang, self.LOCATION_MAP["en"])

        params = {
            "keywords": keyword,
            "location": location,
            "start": 0,
        }

        try:
            response = requests.get(
                self.BASE_URL,
                params=params,
                headers=self.headers,
                timeout=15,
            )
            response.raise_for_status()

            jobs = self._parse_job_listings(response.text, lang)

            logger.info(
                f"LinkedIn scraper found {len(jobs)} jobs for '{keyword}' in {location}"
            )
            return jobs[: self.max_results]

        except Exception as e:
            logger.error(f"Error scraping LinkedIn: {e}")
            return []

    def _parse_job_listings(self, html: str, lang: str) -> List[Dict]:
        """Parse job listings from HTML response."""
        soup = BeautifulSoup(html, "html.parser")
        jobs = []

        # Find all job cards
        job_cards = soup.find_all("div", class_="base-card")

        for card in job_cards:
            try:
                job = self._parse_job_card(card, lang)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.warning(f"Error parsing job card: {e}")
                continue

        return jobs

    def _parse_job_card(self, card, lang: str) -> Optional[Dict]:
        """Parse a single job card element."""
        # Extract job URN from data attribute
        job_urn = card.get("data-entity-urn", "")
        job_id = None
        if job_urn:
            match = re.search(r"jobPosting:(\d+)", job_urn)
            if match:
                job_id = match.group(1)

        # Title
        title_elem = card.find("h3", class_="base-search-card__title")
        title = title_elem.get_text(strip=True) if title_elem else None

        if not title:
            return None

        # Company
        company_elem = card.find("h4", class_="base-search-card__subtitle")
        company_name = company_elem.get_text(strip=True) if company_elem else "Unknown"

        # Company logo
        logo_elem = card.find("img", class_="artdeco-entity-image")
        company_logo = (
            logo_elem.get("data-delayed-url") or logo_elem.get("src")
            if logo_elem
            else None
        )

        # Location
        location_elem = card.find("span", class_="job-search-card__location")
        location_raw = location_elem.get_text(strip=True) if location_elem else None

        # Date posted
        date_elem = card.find("time", class_="job-search-card__listdate")
        if not date_elem:
            date_elem = card.find("time", class_="job-search-card__listdate--new")
        posted_at = date_elem.get("datetime") if date_elem else None

        # Job link
        link_elem = card.find("a", class_="base-card__full-link")
        link = link_elem.get("href") if link_elem else None

        # Clean up link (remove tracking params)
        if link:
            link = link.split("?")[0]

        if not link and job_id:
            link = f"https://www.linkedin.com/jobs/view/{job_id}/"

        if not link:
            return None

        return {
            "title": title,
            "company": {
                "name": company_name,
                "logo": company_logo,
            },
            "description": "",  # Would need to fetch detail page for full description
            "link": link,
            "location_raw": location_raw,
            "source": "LinkedIn",
            "original_language": lang,
            "published_at": posted_at,
            "external_id": job_id,
            "remote": self._is_remote(title, location_raw),
        }

    def _is_remote(self, title: str, location: str) -> bool:
        """Check if job is remote based on title or location."""
        remote_keywords = [
            "remote",
            "remoto",
            "télétravail",
            "homeoffice",
            "home office",
        ]
        text = f"{title} {location}".lower() if location else title.lower()
        return any(kw in text for kw in remote_keywords)

    async def fetch_job_details(self, job_id: str) -> Optional[Dict]:
        """Fetch full job details (description, requirements, etc.)."""
        url = self.JOB_DETAIL_URL.format(job_id=job_id)

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            # Extract description using the most precise container available
            # 0. Most precise: componentkey="JobDetails_AboutTheJob_*" (modern LinkedIn pages)
            #    This targets ONLY the job description block, excluding related jobs, referral
            #    banners, sign-in prompts, and any other LinkedIn noise.
            desc_elem = soup.find(
                attrs={
                    "componentkey": lambda v: v
                    and v.startswith("JobDetails_AboutTheJob_")
                }
            )

            # 1. Fallback: SDUI component attribute
            if not desc_elem:
                desc_elem = soup.find(
                    attrs={
                        "data-sdui-component": "com.linkedin.sdui.generated.jobseeker.dsl.impl.aboutTheJob"
                    }
                )

            # 2. Fallback to the markup container typically found in Guest API
            if not desc_elem:
                desc_elem = soup.find("div", class_="show-more-less-html__markup")

            # 3. Final fallback to the generic description text div
            if not desc_elem:
                desc_elem = soup.find("div", class_="description__text")

            description = ""
            if desc_elem:
                # 1. Remove unwanted elements based on CLASS

                # Remove "Show more/less" buttons and text
                for btn in desc_elem.find_all(class_="show-more-less-html__button"):
                    btn.decompose()

                # Remove "Referral" sections (specific container)
                for ref in desc_elem.find_all(class_="find-a-referral__cta-container"):
                    ref.decompose()

                # Remove "Job Alert" sections (specific container)
                for alert in desc_elem.find_all(class_="job-details-how-to-apply"):
                    alert.decompose()

                # Remove "Similar Jobs" sections (specific container)
                for similar in desc_elem.find_all(
                    class_=lambda x: x and "similar-jobs" in x
                ):
                    similar.decompose()

                # Remove "Contextual Sign In" modal
                for signin in desc_elem.find_all(class_="contextual-sign-in-modal"):
                    signin.decompose()

                # Remove "Job Alert Redirect" section
                for redirect in desc_elem.find_all(class_="job-alert-redirect-section"):
                    redirect.decompose()

                # 2. Remove unwanted elements based on TEXT CONTENT (if classes fail)

                # List of text patterns that indicate a whole block should be removed
                block_removal_patterns = [
                    r"Referrals increase your chances",
                    r"See who you know",
                    r"Get notified about new",
                    r"Sign in to create job alert",
                    r"Similar jobs",
                    r"Similar Searches",
                    r"Explore collaborative articles",
                    r"Explore More",
                    r"People also viewed",
                    r"Job alert",
                    r"Alert set",
                ]

                for pattern in block_removal_patterns:
                    for tag in desc_elem.find_all(
                        text=re.compile(pattern, re.IGNORECASE)
                    ):
                        parent = tag.parent
                        # If found, try to remove the containing block (e.g. div or section)
                        # We climb up slightly to catch the wrapper
                        if parent and parent != desc_elem:
                            # If it's a small inline tag, maybe go one level up
                            if parent.name in ["span", "a", "strong", "em"]:
                                grandparent = parent.parent
                                if (
                                    grandparent
                                    and grandparent != desc_elem
                                    and grandparent.name
                                    in ["div", "section", "p", "li"]
                                ):
                                    grandparent.decompose()
                                else:
                                    parent.decompose()
                            else:
                                parent.decompose()

                # 3. General Cleanup (Buttons, Criteria, etc)

                # Remove remaining buttons
                for btn in desc_elem.find_all("button"):
                    btn.decompose()

                # Remove "Show more/less" text spans if they survived class removal
                for span in desc_elem.find_all(
                    "span",
                    text=re.compile(
                        r"Show (more|less)|Mehr anzeigen|Weniger anzeigen",
                        re.IGNORECASE,
                    ),
                ):
                    span.decompose()

                # Remove job criteria list (often duplicated in description text or just noise)
                for criteria in desc_elem.find_all(
                    "ul", class_="description__job-criteria-list"
                ):
                    criteria.decompose()

                # Also remove the criteria *header* if it exists separately (often h3)
                for header in desc_elem.find_all(
                    "h3",
                    text=re.compile(
                        r"Job criteria|Kriterien|Dettagli offert", re.IGNORECASE
                    ),
                ):
                    header.decompose()

                # Remove specific noise text without removing parent if it's the main desc
                noise_patterns = [
                    r"Mit einer Empfehlung lassen sich",
                    r"Wen kennen Sie bereits",
                    r"Lassen Sie sich benachrichtigen",
                    r"Loggen Sie sich ein",
                    r"Benachrichtigung einstellen",
                    r"Passwort vergessen",
                    r"Neu bei LinkedIn",
                    r"Zum Bewerben",
                    r"E-Mail-Adresse/Telefon",
                    r"Show more",
                    r"Show less",
                    r"Mehr anzeigen",
                    r"Weniger anzeigen",
                    r"Ähnliche Suchen",
                    r"Das könnte Sie auch interessieren",
                    r"Personen haben sich auch angesehen",
                    r"Job-Alarm erstellen",
                    r"Similar searches",
                    r"People also viewed",
                ]

                for pattern in noise_patterns:
                    for tag in desc_elem.find_all(
                        text=re.compile(pattern, re.IGNORECASE)
                    ):
                        tag.replace_with("")

                # 4. Normalize HTML
                cleaned_html = self.normalize_html(str(desc_elem))

                # 5. Convert to Markdown
                from markdownify import markdownify as md

                description = md(cleaned_html, heading_style="atx").strip()

                # Post-processing cleanup
                lines = description.split("\n")
                cleaned_lines = []

                # Truncation triggers: If we see these, DISCARD EVERYTHING AFTER
                truncation_triggers = [
                    "similar jobs",
                    "similar searches",
                    "ähnliche jobs",
                    "ähnliche suchen",
                    "explore collaborative articles",
                    "explore more",
                    "people also viewed",
                    "ebenfalls angesehen",
                    "personen haben sich auch angesehen",
                    "das potrebbe interessarti",
                    "das könnte sie auch interessieren",
                    "sign in to create job alert",
                    "job-alarm erstellen",
                    "get notified about new",
                    "referrals increase your chances",
                    "see who you know",
                    "mit einer empfehlung lassen sich",
                    "wen kennen sie bereits",
                ]

                # Skip triggers: If we see these, JUST SKIP THE LINE
                skip_triggers = [
                    "show more",
                    "show less",
                    "mehr anzeigen",
                    "weniger anzeigen",
                    "einblenden",
                    "ausblenden",
                    "seniority level",
                    "employment type",
                    "job function",
                    "industries",
                    "karrierestufe",
                    "beschäftigungsverhältnis",
                    "tätigkeitsbereich",
                    "branchen",
                ]

                for line in lines:
                    s_line = line.strip()
                    if not s_line:
                        cleaned_lines.append(line)
                        continue

                    lower_line = s_line.lower()

                    # 1. Check for TRUNCATION (Hard stop)
                    should_truncate = False
                    for trigger in truncation_triggers:
                        # Check if line IS the trigger or STARTS with it (for headers like "### Similar jobs")
                        if (
                            lower_line == trigger
                            or lower_line.startswith(trigger)
                            or (lower_line.startswith("### ") and trigger in lower_line)
                        ):
                            should_truncate = True
                            break

                    if should_truncate:
                        # Stop processing completely
                        break

                    # 2. Check for SKIP (Individual line removal)
                    should_skip = False
                    for trigger in skip_triggers:
                        if lower_line == trigger or lower_line.startswith(trigger):
                            should_skip = True
                            break

                    if should_skip:
                        continue

                    cleaned_lines.append(line)

                description = "\n".join(cleaned_lines).strip()

            # Extract criteria
            criteria = {}
            criteria_list = soup.find_all("li", class_="description__job-criteria-item")
            for item in criteria_list:
                header = item.find("h3")
                value = item.find("span")
                if header and value:
                    key = header.get_text(strip=True).lower().replace(" ", "_")
                    criteria[key] = value.get_text(strip=True)

            return {
                "description": description,
                "seniority": criteria.get("seniority_level"),
                "employment_type": criteria.get("employment_type"),
                "job_function": criteria.get("job_function"),
                "industries": criteria.get("industries"),
            }

        except Exception as e:
            logger.warning(f"Error fetching job details for {job_id}: {e}")
            return None
