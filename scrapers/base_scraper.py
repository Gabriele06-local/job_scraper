from abc import ABC, abstractmethod
from typing import List, Dict
from bs4 import BeautifulSoup
import structlog

logger = structlog.get_logger(__name__)


class BaseScraper(ABC):
    @abstractmethod
    async def scrape(self, keyword: str, lang: str) -> List[Dict]:
        pass

    def normalize_html(self, html_content: str) -> str:
        """
        Normalize HTML content for SEO and Markdown conversion:
        1. Ensures heading hierarchy starts at H2 (H1 is for page title).
        2. Fixes skipped heading levels (e.g., H2 -> H4 becomes H2 -> H3).
        3. Removes empty tags.
        4. Ensures all images have alt attributes.
        """
        if not html_content:
            return ""

        try:
            soup = BeautifulSoup(html_content, "html.parser")

            # 1. Remove empty tags (except media/void tags)
            for tag in soup.find_all():
                if len(tag.get_text(strip=True)) == 0 and tag.name not in [
                    "img",
                    "br",
                    "hr",
                    "input",
                    "source",
                    "link",
                    "meta",
                ]:
                    tag.decompose()

            # 2. Ensure images have alt attributes
            for img in soup.find_all("img"):
                if not img.get("alt"):
                    img["alt"] = "Job description image"

            # 3. Normalize Headings
            # - Demote H1 to H2
            for h1 in soup.find_all("h1"):
                h1.name = "h2"

            # - Fix hierarchy (H2 -> H3 -> H4...)
            # This is a complex problem to solve perfectly without context,
            # but we can ensure a relative order.
            # A simple robust approach for job descriptions:
            # Map existing headings to a normalized sequence starting from H2.

            # Find all headings in document order
            headings = soup.find_all(["h2", "h3", "h4", "h5", "h6"])

            # If we have headings, ensuring the first one is H2 is a good start
            if headings and headings[0].name != "h2":
                headings[0].name = "h2"

            # Iterate and ensure we don't skip levels (e.g. H2 -> H4)
            # We track the current "depth".
            # H2 = level 2, H3 = level 3, etc.

            # Note: A full rigorous re-structure is risky as it might break semantic grouping.
            # For now, ensuring no H1 exists is the most critical SEO fix for descriptions.
            # And preventing massive jumps (H2 -> H5).

            last_level = 2
            for h in headings:
                try:
                    current_level = int(h.name[1])
                except (ValueError, IndexError):
                    continue

                # If we jump down more than 1 level (e.g. 2 -> 4), clamp it (2 -> 3)
                if current_level > last_level + 1:
                    h.name = f"h{last_level + 1}"
                    last_level = last_level + 1
                else:
                    last_level = current_level

            return str(soup)

        except Exception as e:
            logger.warning("base_scraper.normalize_html_error", error=str(e))
            return html_content

    def clean_description(self, text: str) -> str:
        """
        Sanitize description to remove images and potentially unsafe/unwanted tags
        while preserving text and structure.
        """
        if not text:
            return ""

        try:
            # Check if text looks like HTML
            if not ("<" in text and ">" in text):
                return text

            soup = BeautifulSoup(text, "html.parser")

            # Remove images and media
            for tag in soup.find_all(
                [
                    "img",
                    "svg",
                    "figure",
                    "picture",
                    "video",
                    "audio",
                    "iframe",
                    "object",
                    "embed",
                    "script",
                    "style",
                ]
            ):
                tag.decompose()

            # Remove elements with v: prefixes (VML) often found in RSS
            # BeautifulSoup with html.parser might handle namespaces poorly, but we can try to catch common ones
            # or just iterate all and check name

            return str(soup)
        except Exception as e:
            logger.warning("base_scraper.clean_description_error", error=str(e))
            return text
