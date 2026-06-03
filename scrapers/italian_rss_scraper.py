"""Generic Italian RSS scraper — parametrized by feed URL."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import List, Dict

import requests
import structlog
from bs4 import BeautifulSoup

from utils.description_fetcher import DescriptionFetcher

from .base_scraper import BaseScraper

logger = structlog.get_logger(__name__)

# Feeds that ship thin/empty <description> bodies (e.g. AAAnnunci, JobNetwork)
# would be dropped by the pipeline pre-filter (MIN_DESCRIPTION_LEN). For any
# item below this visible-text threshold we fetch the full posting page.
_MIN_DESC_LEN = 200
_ENRICH_CONCURRENCY = 8

_LOCALE_TZ_OFFSETS: dict[str, int] = {
    "CET": 1,
    "CEST": 2,
}

_MONTHS_IT: dict[str, int] = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}


def _parse_date(text: str) -> datetime | None:
    """Try common Italian RSS date formats."""
    if not text:
        return None
    text = text.strip()
    # RFC 2822
    try:
        return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        pass
    # Italian: "Lunedì, 3 Giugno 2026 09:30"
    text_clean = re.sub(r"^[^,]*,\s*", "", text)
    try:
        return datetime.strptime(text_clean, "%d %B %Y %H:%M")
    except ValueError:
        pass
    # Space-separated datetime (e.g. JobNetwork <data>: "2026-03-27 17:06:51").
    # datetime.fromisoformat only accepts the space separator on Python 3.11+,
    # so parse it explicitly for the 3.8 runtime on the VPS.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # ISO 8601
    try:
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        pass
    return None


def _extract_company(item: BeautifulSoup, title: str) -> str:
    creators = [
        item.find("dc:creator"),
        item.find("author"),
        item.find("media:credit"),
    ]
    for tag in creators:
        if tag and tag.text.strip():
            return tag.text.strip()
    m = re.match(r"^(.*?)\s*[–—-]\s*", title)
    if m:
        candidate = m.group(1).strip()
        skip = {"remote", "hiring", "new", "job", "offerta", "lavoro"}
        if candidate.lower() not in skip:
            return candidate
    return "Unknown"


class ItalianRSSScraper(BaseScraper):
    """Scrape a single Italian RSS feed."""

    def __init__(self, feed_url: str, source_name: str = "ItalianRSS"):
        self.feed_url = feed_url
        self._source_name = source_name

    # Header reused for both the feed request and page enrichment.
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
    }

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        if lang not in ("it", "en"):
            return []
        try:
            resp = requests.get(self.feed_url, headers=self._HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "xml")
            items = soup.find_all("item")

            jobs: List[Dict] = []
            for item in items:
                title = item.find("title").text if item.find("title") else ""
                desc_raw = item.find("description").text if item.find("description") else ""
                link = item.find("link").text if item.find("link") else ""
                if not (title and link):
                    continue
                jobs.append({
                    "title": title,
                    "company_name": _extract_company(item, title),
                    "description": self.clean_description(desc_raw),
                    "url": link,
                    "source": self._source_name,
                    "original_language": "it",
                    "published_at": _parse_date(
                        item.find("pubDate").text if item.find("pubDate") else None
                    ),
                    "location_raw": "",
                })

            await self._enrich_descriptions(jobs)
            logger.info(
                "italian_rss.fetched",
                source=self._source_name,
                count=len(jobs),
            )
            return jobs
        except Exception as e:
            logger.error("italian_rss.error", source=self._source_name, error=str(e))
            return []

    @staticmethod
    def _visible_len(html_or_text: str) -> int:
        """Length of the rendered text, ignoring markup."""
        if not html_or_text:
            return 0
        return len(BeautifulSoup(html_or_text, "html.parser").get_text(strip=True))

    async def _enrich_descriptions(self, jobs: List[Dict]) -> None:
        """Fetch full page descriptions for items whose feed body is too thin.

        Mutates `jobs` in place. Bounded concurrency; failures leave the
        original (short) description untouched so the pre-filter can still
        drop genuinely empty postings.
        """
        targets = [j for j in jobs if self._visible_len(j.get("description", "")) < _MIN_DESC_LEN]
        if not targets:
            return

        fetcher = DescriptionFetcher()
        sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

        async def _one(job: Dict) -> None:
            async with sem:
                try:
                    description, _logo = await fetcher.fetch(job["url"])
                except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                    logger.warning(
                        "italian_rss.enrich_failed",
                        source=self._source_name,
                        url=job.get("url"),
                        error=str(exc),
                    )
                    return
                if description and len(description) > self._visible_len(job.get("description", "")):
                    job["description"] = description

        await asyncio.gather(*(_one(j) for j in targets))
        logger.info(
            "italian_rss.enriched",
            source=self._source_name,
            enriched=len(targets),
        )
