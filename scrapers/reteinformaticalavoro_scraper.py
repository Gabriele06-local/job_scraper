import re
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from .base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://reteinformaticalavoro.it"
LISTING_URL = f"{BASE_URL}/offerte-di-lavoro"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

# Regex to capture "RAL: Da €23000 A €27000" or "RAL: Da €23.000 A €27.000"
# or "RAL: €23000 - €27000" or "RAL: €45000"
_RAL_RANGE_RE = re.compile(
    r"RAL[:\s]+(?:Da\s+)?[€£$]?\s*([\d.,]+)\s*(?:A|[-–])\s*[€£$]?\s*([\d.,]+)",
    re.IGNORECASE,
)
_RAL_SINGLE_RE = re.compile(
    r"RAL[:\s]+[€£$]?\s*([\d.,]+)",
    re.IGNORECASE,
)
_PUB_DATE_RE = re.compile(r"Pubblicato\s+(\d{2}-\d{2}-\d{4})", re.IGNORECASE)


def _parse_salary(value: str) -> Optional[float]:
    """Convert a salary string like '23.000' or '23000' to a float."""
    try:
        return float(value.replace(".", "").replace(",", ""))
    except (ValueError, AttributeError):
        return None


class ReteInformaticaLavoroScraper(BaseScraper):
    """
    Scraper for reteinformaticalavoro.it.

    Strategy:
      1. Fetch listing pages filtered by keyword (pagination up to max_pages).
      2. For every job link found, fetch the detail page to extract the full
         description, RAL (salary), metadata and skills — no data is left on
         the listing card.

    The `original_language` is always "it" because the site is Italian-only.
    The scraper runs for every `lang` passed by the orchestrator: since the
    keyword list uses universal tech terms (java, react, devops …) the Italian
    site matches them correctly, and the deduplicator prevents duplicate
    insertions across language passes.
    """

    def __init__(self, max_pages: int = 3, delay_between_requests: float = 0.4):
        self.max_pages = max_pages
        self.delay = delay_between_requests

    # ------------------------------------------------------------------ #
    # Public interface                                                      #
    # ------------------------------------------------------------------ #

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        job_links = self._collect_listing_links(keyword)
        if not job_links:
            return []

        jobs: List[Dict] = []
        for link in job_links:
            time.sleep(self.delay)
            job = self._fetch_detail(link)
            if job:
                jobs.append(job)

        logger.info(
            f"[ReteinformaticaLavoro] keyword='{keyword}' lang={lang} → "
            f"{len(jobs)} jobs fetched"
        )
        return jobs

    # ------------------------------------------------------------------ #
    # Step 1 — collect job URLs from listing pages                         #
    # ------------------------------------------------------------------ #

    def _collect_listing_links(self, keyword: str) -> List[str]:
        links: List[str] = []
        for page in range(1, self.max_pages + 1):
            params = {"cerca": keyword, "page": page}
            try:
                resp = requests.get(
                    LISTING_URL, params=params, headers=HEADERS, timeout=12
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.error(
                    f"[ReteinformaticaLavoro] listing fetch error "
                    f"(keyword='{keyword}', page={page}): {exc}"
                )
                break

            soup = BeautifulSoup(resp.content, "html.parser")
            page_links = self._parse_listing_page(soup)
            if not page_links:
                break  # no more results
            links.extend(page_links)

            # respect server — sleep between pages (not after the last one)
            if page < self.max_pages:
                time.sleep(self.delay)

        return links

    def _parse_listing_page(self, soup: BeautifulSoup) -> List[str]:
        """Extract absolute job detail URLs from a listing page."""
        links: List[str] = []
        # Job cards have an <a> whose href matches /lavoro/{id}/{slug}
        for a_tag in soup.find_all("a", href=re.compile(r"^/lavoro/\d+")):
            href = a_tag.get("href", "").strip()
            if not href:
                continue
            absolute = href if href.startswith("http") else f"{BASE_URL}{href}"
            if absolute not in links:
                links.append(absolute)
        return links

    # ------------------------------------------------------------------ #
    # Step 2 — extract all data from the detail page                       #
    # ------------------------------------------------------------------ #

    def _fetch_detail(self, url: str) -> Optional[Dict]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[ReteinformaticaLavoro] detail fetch error ({url}): {exc}")
            return None

        soup = BeautifulSoup(resp.content, "html.parser")
        return self._parse_detail_page(soup, url)

    def _parse_detail_page(self, soup: BeautifulSoup, url: str) -> Optional[Dict]:
        # ---- title ---------------------------------------------------- #
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
        if not title:
            logger.warning(f"[ReteinformaticaLavoro] no title found at {url}")
            return None

        # ---- company name & logo -------------------------------------- #
        company_name = "Unknown"
        company_logo: Optional[str] = None

        company_link = soup.find("a", href=re.compile(r"/azienda/\d+"))
        if company_link:
            company_name = company_link.get_text(strip=True)

        logo_img = soup.find("img", src=re.compile(r"/images/company/"))
        if logo_img:
            src = logo_img.get("src", "")
            company_logo = src if src.startswith("http") else f"{BASE_URL}{src}"

        # ---- full page text for metadata extraction ------------------- #
        page_text = soup.get_text(" ", strip=True)

        # ---- location ------------------------------------------------- #
        location_raw = self._extract_location(soup)

        # ---- remote working ------------------------------------------- #
        remote = self._extract_remote(page_text)

        # ---- employment type ------------------------------------------ #
        employment_type = self._extract_meta_value(
            page_text, r"Tipo\s+di\s+Offerta\s*:?\s*([^\n,|]+?)(?=Tipo\s+Contratto|Seniority|RAL|Esperienza|$)"
        )

        # ---- salary (RAL) --------------------------------------------- #
        salary_min, salary_max = self._extract_salary(page_text)

        # ---- published date ------------------------------------------- #
        published_at = self._extract_date(page_text)

        # ---- skill tags ----------------------------------------------- #
        skill_tags = self._extract_skill_tags(soup)

        # ---- description (main body) ---------------------------------- #
        description = self._extract_description(soup, skill_tags)
        if not description:
            logger.warning(f"[ReteinformaticaLavoro] empty description at {url}")
            return None

        return {
            "title": title,
            "link": url,
            "description": self.clean_description(description),
            "company": {
                "name": company_name,
                "logo": company_logo,
            },
            "location_raw": location_raw,
            "remote": remote,
            "employment_type": employment_type,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "published_at": published_at,
            "source": "ReteinformaticaLavoro",
            "original_language": "it",
        }

    # ------------------------------------------------------------------ #
    # Field-level parsers                                                   #
    # ------------------------------------------------------------------ #

    def _extract_location(self, soup: BeautifulSoup) -> str:
        """
        Find the city token near the company link.
        The pattern on the page is: [CompanyName] CityName (XX) Candidati Subito
        """
        city_link = soup.find("a", href=re.compile(r"/offerte-di-lavoro/[a-z]"))
        if city_link:
            return city_link.get_text(strip=True)
        return ""

    def _extract_remote(self, page_text: str) -> bool:
        pattern = re.search(
            r"Remote\s*working\s*:\s*(Totale|Parziale)", page_text, re.IGNORECASE
        )
        return bool(pattern)

    def _extract_meta_value(self, page_text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _extract_salary(self, page_text: str) -> tuple[Optional[float], Optional[float]]:
        range_match = _RAL_RANGE_RE.search(page_text)
        if range_match:
            return _parse_salary(range_match.group(1)), _parse_salary(range_match.group(2))
        single_match = _RAL_SINGLE_RE.search(page_text)
        if single_match:
            val = _parse_salary(single_match.group(1))
            return val, val
        return None, None

    def _extract_date(self, page_text: str) -> Optional[datetime]:
        match = _PUB_DATE_RE.search(page_text)
        if match:
            try:
                return datetime.strptime(match.group(1), "%d-%m-%Y")
            except ValueError:
                pass
        return None

    def _extract_skill_tags(self, soup: BeautifulSoup) -> List[str]:
        """
        Skill tags are <a> elements linking to /offerte-di-lavoro/estero/{tag}.
        """
        tags: List[str] = []
        for a_tag in soup.find_all("a", href=re.compile(r"/offerte-di-lavoro/estero/")):
            text = a_tag.get_text(strip=True)
            if text:
                tags.append(text)
        return list(dict.fromkeys(tags))  # preserve order, deduplicate

    def _extract_description(self, soup: BeautifulSoup, skill_tags: List[str]) -> str:
        """
        Extract the job description body, stripping navigation, cookie banners,
        footer, sidebar and the 'altre offerte' related section.

        Strategy:
          - Remove known noise elements by tag/class
          - Find the content area that comes after the metadata block
            (identified by the 'Pubblicato DD-MM-YYYY' text node)
          - Prepend structured metadata (skills, etc.) so the AI has full context
        """
        # 1. Clone the soup to avoid mutating the original
        working = BeautifulSoup(str(soup), "html.parser")

        # 2. Remove noise elements
        noise_selectors = [
            "header", "footer", "nav",
            "[class*='cookie']", "[id*='cookie']",
            "[class*='CookieBanner']", "[id*='CookieBanner']",
            "[class*='navbar']", "[class*='sidebar']",
            "[class*='modal']", "[class*='consent']",
            "[class*='altre-offerte']", "[class*='related']",
            "script", "style", "noscript",
        ]
        for selector in noise_selectors:
            for el in working.select(selector):
                el.decompose()

        # 3. Look for the "Pubblicato" date text node as anchor point
        pub_node = working.find(string=_PUB_DATE_RE)

        if pub_node:
            # Collect all text after the publication date node
            parts: List[str] = []
            for sibling in pub_node.parent.next_siblings:
                text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
                if not text:
                    continue
                # Stop at "Condividi" or the related jobs section
                if re.search(r"^condividi|altre\s+offerte", text, re.IGNORECASE):
                    break
                parts.append(text)

            description = "\n\n".join(p for p in parts if p)
        else:
            # Fallback: get all text from the working tree, cleaned
            raw = working.get_text("\n", strip=True)
            # Heuristic: keep only content that looks like job description
            description = raw

        # 4. Prepend skill tags as a structured line for AI context
        if skill_tags:
            skill_line = "Competenze/Tag: " + ", ".join(skill_tags)
            description = skill_line + "\n\n" + description if description else skill_line

        return description.strip()
