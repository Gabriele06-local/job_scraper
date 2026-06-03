"""JobNetwork scraper — custom <annuncio> XML schema.

JobNetwork's public feed (https://www.jobnetwork.it/rss/homepage.xml) is NOT a
standard RSS document: the root is <annunci>, each posting is an <annuncio>
with <posizione>/<link>/<data>/<tipo>/<prov> children and no <description>.
This scraper maps that schema onto the canonical job dict and reuses the
ItalianRSSScraper page-enrichment to backfill the missing description.
"""

from __future__ import annotations

import re
from typing import Dict, List

import requests
import structlog
from bs4 import BeautifulSoup

from .italian_rss_scraper import ItalianRSSScraper, _parse_date

logger = structlog.get_logger(__name__)

# The feed ships a malformed DOCTYPE (broken parameter-entity reference) that
# makes the strict XML parser abort with zero nodes. Strip it before parsing.
_DOCTYPE_RE = re.compile(r"<!DOCTYPE.*?\]>", re.DOTALL)


class JobNetworkScraper(ItalianRSSScraper):
    """Scrape the JobNetwork <annuncio> XML feed."""

    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        if lang not in ("it", "en"):
            return []
        try:
            resp = requests.get(self.feed_url, headers=self._HEADERS, timeout=15)
            resp.raise_for_status()
            cleaned = _DOCTYPE_RE.sub("", resp.content.decode("utf-8", "replace"))
            soup = BeautifulSoup(cleaned, "xml")
            items = soup.find_all("annuncio")

            jobs: List[Dict] = []
            for item in items:
                posizione = item.find("posizione")
                link = item.find("link")
                title = posizione.text.strip() if posizione else ""
                url = link.text.strip() if link else ""
                if not (title and url):
                    continue

                prov = item.find("prov")
                tipo = item.find("tipo")
                data = item.find("data")
                source_hints: Dict[str, str] = {}
                if tipo and tipo.text.strip():
                    source_hints["employment_type"] = tipo.text.strip()

                jobs.append({
                    "title": title,
                    "company_name": "Unknown",
                    # No description in the feed — enrichment backfills from the page.
                    "description": "",
                    "url": url,
                    "source": self._source_name,
                    "original_language": "it",
                    "published_at": _parse_date(data.text if data else None),
                    "location_raw": prov.text.strip() if prov else "",
                    "source_hints": source_hints or None,
                })

            await self._enrich_descriptions(jobs)
            logger.info(
                "jobnetwork.fetched",
                source=self._source_name,
                count=len(jobs),
            )
            return jobs
        except Exception as e:
            logger.error("jobnetwork.error", source=self._source_name, error=str(e))
            return []
