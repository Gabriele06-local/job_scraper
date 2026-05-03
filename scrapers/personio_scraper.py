"""Personio ATS XML scraper — no auth required.

Parses structured XML (not HTML). EU-focused companies.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://{slug}.jobs.personio.de/xml"
_TIMEOUT = 10


class PersonioScraper:
    def fetch(self, companies: list[dict[str, str]]) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for company in companies:
            slug = company["slug"]
            name = company["name"]
            try:
                resp = requests.get(_BASE_URL.format(slug=slug), timeout=_TIMEOUT)
                resp.raise_for_status()
                items = self._parse_xml(resp.content, name)
                log.debug("personio.fetched", company=name, count=len(items))
                jobs.extend(items)
            except requests.RequestException as exc:
                log.error("personio.fetch_error", company=name, error=str(exc)[:120])
            except ET.ParseError as exc:
                log.error("personio.xml_parse_error", company=name, error=str(exc))
            time.sleep(0.3)
        return jobs

    def _parse_xml(self, content: bytes, company_name: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        root = ET.fromstring(content)

        for pos in root.iter("position"):
            title = self._text(pos, "name") or self._text(pos, "title") or ""
            job_id = self._text(pos, "id") or ""
            url = f"https://{company_name.lower().replace(' ', '')}.jobs.personio.de/job/{job_id}" if job_id else ""
            if not (title and url):
                continue

            office = self._text(pos, "office") or self._text(pos, "location") or ""
            description_parts = []
            for tag in ("jobDescriptions", "description"):
                desc_el = pos.find(tag)
                if desc_el is not None:
                    description_parts.append("".join(desc_el.itertext()))
            description = "\n".join(description_parts)

            jobs.append({
                "title": title,
                "company_name": company_name,
                "description": description,
                "url": url,
                "source": "Personio",
                "original_language": "de",
                "published_at": None,
                "location_raw": office,
                "salary_min": None,
                "salary_max": None,
                "external_id": job_id,
            })
        return jobs

    @staticmethod
    def _text(element: ET.Element, tag: str) -> str | None:
        el = element.find(tag)
        return el.text.strip() if el is not None and el.text else None
