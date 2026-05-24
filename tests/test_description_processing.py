import pytest
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import sys
import os

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.description_fetcher import DescriptionFetcher


class TestDescriptionProcessing:
    @pytest.fixture
    def fetcher(self):
        return DescriptionFetcher()

    def test_extract_content_logo_preservation(self, fetcher):
        html = f"""
        <div class="job-description">
            <img src="https://example.com/logo.png" alt="Logo">
            <h1>Job Title</h1>
            <p>Description text. {"Lorem ipsum " * 100}</p>
        </div>
        """
        markdown, logo_url = fetcher._extract_content(html)

        assert logo_url == "https://example.com/logo.png"
        assert "Logo" not in markdown
        assert "Job Title" in markdown
        assert "Description text." in markdown

    def test_extract_content_strip_middle_images(self, fetcher):
        html = f"""
        <div class="job-description">
            <h1>Job Title</h1>
            <p>Some text. {"Lorem ipsum " * 50}</p>
            <img src="https://example.com/ad.jpg" alt="Advertisement">
            <p>More text. {"Lorem ipsum " * 50}</p>
        </div>
        """
        markdown, logo_url = fetcher._extract_content(html)

        assert logo_url is None
        assert "Job Title" in markdown
        assert "Some text." in markdown
        assert "More text." in markdown
        assert "![" not in markdown
        assert "Advertisement" not in markdown

    def test_extract_content_logo_plus_other_images(self, fetcher):
        html = f"""
        <div class="job-description">
            <img src="https://example.com/logo.png" alt="Logo">
            <p>Intro. {"Lorem ipsum " * 50}</p>
            <img src="https://example.com/banner.jpg" alt="Banner">
            <p>Outro. {"Lorem ipsum " * 50}</p>
        </div>
        """
        markdown, logo_url = fetcher._extract_content(html)

        assert logo_url == "https://example.com/logo.png"
        assert "Intro." in markdown
        assert "Outro." in markdown
        assert "![" not in markdown
        assert "Banner" not in markdown

    def test_main_loop_html_stripping(self):
        # Semi-unit test for the logic added to main.py
        html_desc = """
        <div>
            <p>HTML snippet with image.</p>
            <img src="https://example.com/image.png" alt="Image">
        </div>
        """

        soup = BeautifulSoup(html_desc, "html.parser")
        if bool(soup.find()):
            for img in soup.find_all("img"):
                img.decompose()
            result_md = md(str(soup))

        assert "HTML snippet" in result_md
        assert "![" not in result_md
        assert "image.png" not in result_md
