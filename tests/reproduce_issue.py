from bs4 import BeautifulSoup
import unittest


class TestLinkedInScraper(unittest.TestCase):
    def test_description_cleaning(self):
        html = """
        <div class="description__text description__text--rich">
            <section class="show-more-less-html" data-max-num-lines="5">
                <div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">
                    <p><strong>Job Description</strong></p>
                    <p>This is the actual job description text that we want to keep.</p>
                    
                    <button class="show-more-less-html__button show-more-less-button show-more-less-html__button--more" data-tracking-control-name="public_jobs_show-more-html-btn">
                        Show more
                    </button>
            
                    <button class="show-more-less-html__button show-more-less-button show-more-less-html__button--less" data-tracking-control-name="public_jobs_show-less-html-btn">
                        Show less
                    </button>
                </div>
            </section>

            <ul class="description__job-criteria-list">
                <li class="description__job-criteria-item">
                    <h3 class="description__job-criteria-subheader">Nivel de antigüedad</h3>
                    <span class="description__job-criteria-text description__job-criteria-text--criteria">No corresponde</span>
                </li>
                <li class="description__job-criteria-item">
                    <h3 class="description__job-criteria-subheader">Tipo de empleo</h3>
                    <span class="description__job-criteria-text description__job-criteria-text--criteria">Jornada completa</span>
                </li>
            </ul>
            
            <div class="promo-content">
                Las recomendaciones duplican tus probabilidades de conseguir una entrevista con Qindel Group
            </div>
        </div>
        """

        soup = BeautifulSoup(html, "html.parser")
        desc_elem = soup.find("div", class_="description__text")

        # Current logic
        description = desc_elem.get_text(separator="\n", strip=True)

        print("--- Extracted Description ---")
        print(description)
        print("-----------------------------")

        # Assertions to confirm the issue
        self.assertIn("Show more", description)
        self.assertIn("Show less", description)
        self.assertIn("Nivel de antigüedad", description)

        # New logic proposal (to be implemented)
        # 1. Remove "Show more" / "Show less" buttons
        for btn in desc_elem.find_all("button"):
            btn.decompose()

        # 2. Remove criteria list if present inside description__text
        for criteria in desc_elem.find_all("ul", class_="description__job-criteria-list"):
            criteria.decompose()

        # 3. Remove other unwanted sections
        # This part might need adjustment based on actual HTML structure,
        # but for now let's see if the above fixes the main issue.

        cleaned_description = desc_elem.get_text(separator="\n", strip=True)
        print("--- Cleaned Description (Proposed) ---")
        print(cleaned_description)
        print("--------------------------------------")

    def test_markdown_conversion_and_normalization(self):
        """Test that HTML is correctly normalized and converted to Markdown."""
        from markdownify import markdownify as md

        html = """
        <div class="description__text">
            <h1>Job Title (Should be H2)</h1>
            <p>Intro text.</p>
            <h4>Skipped level (Should be H3)</h4>
            <p>Details.</p>
            
            <p><strong>Bold text</strong></p>
            <ul>
                <li>List item 1</li>
                <li>List item 2</li>
            </ul>
             <button>Show more</button>
        </div>
        """

        soup = BeautifulSoup(html, "html.parser")
        desc_elem = soup.find("div", class_="description__text")

        # Simulating the logic implemented in LinkedInScraper

        # 1. Remove unwanted elements
        for btn in desc_elem.find_all("button"):
            btn.decompose()

        # 2. Normalize HTML (Mocking BaseScraper.normalize_html behavior for this test)
        # In real app this comes from inheritance

        # - Remove empty tags
        for tag in desc_elem.find_all():
            if len(tag.get_text(strip=True)) == 0 and tag.name not in [
                "img",
                "br",
                "hr",
            ]:
                tag.decompose()

        # - Ensure H1 -> H2
        for h1 in desc_elem.find_all("h1"):
            h1.name = "h2"

        # - Ensure alt
        for img in desc_elem.find_all("img"):
            if not img.get("alt"):
                img["alt"] = "Job description image"

        # 3. Convert
        markdown = md(str(desc_elem), heading_style="atx").strip()

        print("\n--- Converted Markdown (Final Logic) ---")
        print(markdown)
        print("----------------------------------------")

        # Check that H1 was converted to H2
        # We ensure that no line starts with a single hashtag followed by space
        self.assertNotIn("\n# Job Title", "\n" + markdown)
        self.assertIn("## Job Title", markdown)
        self.assertNotIn("Show more", markdown)
        self.assertIn("* List item 1", markdown)
        self.assertIn("**Bold text**", markdown)


if __name__ == "__main__":
    unittest.main()
