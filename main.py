#!/usr/bin/env python3
import os
import sys
from bs4 import BeautifulSoup
import logging
import asyncio
import argparse
from datetime import datetime, date
from dotenv import load_dotenv

from database.mongo_client import MongoDBClient
from ai.categorizer import JobCategorizer
from utils.deduplicator import JobDeduplicator
from utils.geocoding import Geocoder
from utils.description_fetcher import DescriptionFetcher
from markdownify import markdownify as md
from scrapers.adzuna_scraper import AdzunaScraper
from scrapers.rss_scraper import RSSScraper
from scrapers.jobisjob_scraper import JobisJobScraper
from scrapers.jooble_scraper import JoobleScraper
from scrapers.remoteok_scraper import RemoteOKScraper
from scrapers.arbeitnow_scraper import ArbeitnowScraper
from scrapers.jobicy_scraper import JobicyScraper
from scrapers.iprogrammatori_scraper import IProgrammatoriScraper
from scrapers.reteinformaticalavoro_scraper import ReteInformaticaLavoroScraper
from scrapers.linkedin_scraper import LinkedInScraper
from scrapers.techmap_scraper import TechMapScraper
from scrapers.jobscollider_scraper import JobsColliderScraper

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("job_scraper.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


class JobScraperOrchestrator:
    def __init__(self, languages=None, limit_per_language=None, days_window=1):
        # Database connection using DATABASE_URL (with MONGO_URI fallback)
        # The database name will be extracted from the connection string
        self.db_client = MongoDBClient()
        self.categorizer = JobCategorizer(
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        )
        self.geocoder = Geocoder(api_key=os.getenv("GOOGLE_MAPS_API_KEY"))
        self.deduplicator = JobDeduplicator(self.db_client)
        self.description_fetcher = DescriptionFetcher()
        self.days_window = days_window

        # ... (imports)

        # Initialize scrapers
        self.scrapers = [
            LinkedInScraper(),  # LinkedIn FIRST (uses free public API)
            IProgrammatoriScraper(),
            ReteInformaticaLavoroScraper(),
            ArbeitnowScraper(),
            JobicyScraper(),
            RemoteOKScraper(),
            AdzunaScraper(
                app_id=os.getenv("ADZUNA_APP_ID"), app_key=os.getenv("ADZUNA_APP_KEY")
            ),
            JobisJobScraper(),
            JoobleScraper(api_key=os.getenv("JOOBLE_API_KEY")),
            RSSScraper(
                rss_urls={
                    "en": [
                        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
                        "https://himalayas.app/jobs/rss",
                        "https://remotive.io/remote-jobs/feed",
                        "https://jobicy.com/feed",
                    ],
                }
            ),
            # TechMapScraper(api_token=os.getenv("TECHMAP_API_TOKEN")),
            # JobsColliderScraper(api_token=os.getenv("JOBSCOLLIDER_API_TOKEN")),
        ]

        self.languages = languages or ["en", "it", "es", "fr", "de"]
        self.limit_per_language = limit_per_language
        self.keywords = [
            "software engineer",
            "software developer",
            "web developer",
            "frontend",
            "backend",
            "fullstack",
            "devops",
            "mobile developer",
            "data scientist",
            "data engineer",
            "cloud engineer",
            "sysadmin",
            "cybersecurity",
            "java",
            "python",
            "javascript",
            "typscript",
            "golang",
            "rust",
            "c++",
            "c#",
            ".net",
            "php",
            "ruby",
            "kotlin",
            "swift",
            "react",
            "angular",
            "vue",
            "node.js",
            "django",
            "spring boot",
            "solidity",
            "blockchain",
            "machine learning",
            "ai engineer",
            "programmatore",
            "sviluppatore",
            "laravel",
        ]

        # Statistics tracking
        self.stats = {}

    def parse_date(self, pub_date):
        if not pub_date:
            return None

        if pub_date == "older":
            return None

        if isinstance(pub_date, datetime):
            if pub_date.tzinfo:
                return pub_date.replace(tzinfo=None)
            return pub_date

        if isinstance(pub_date, date):
            return datetime.combine(pub_date, datetime.min.time())

        if isinstance(pub_date, str):
            pub_date = pub_date.strip()
            fmts = [
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%d",
                "%d %b %Y",
            ]
            for fmt in fmts:
                try:
                    dt = datetime.strptime(pub_date, fmt)
                    if dt.tzinfo:
                        dt = dt.replace(tzinfo=None)
                    return dt
                except ValueError:
                    continue

            # Fallback: check if the string contains today's date in YYYY-MM-DD format
            today_str = date.today().strftime("%Y-%m-%d")
            if today_str in pub_date:
                return datetime.now()

        return None

    def is_published_today(self, pub_date, days_window=1):
        dt = self.parse_date(pub_date)
        if not dt:
            # If we can't parse it but it's not None, maybe it's just "today" or similar
            if isinstance(pub_date, str) and (
                "today" in pub_date.lower() or "oggi" in pub_date.lower()
            ):
                return True
            return False

        diff = (datetime.now() - dt).days
        return diff <= days_window

    def is_relevant_job(self, title: str) -> bool:
        """Check if job title matches our target keywords"""
        if not title:
            return False
        title_lower = title.lower()
        for k in self.keywords:
            if k.lower() in title_lower:
                return True
        return False

    async def process_job_list(self, jobs, lang, lang_count):
        for job in jobs:
            if self.limit_per_language and lang_count >= self.limit_per_language:
                break

            # GLOBAL VALIDATION: Skip jobs without a link
            if not job.get("link"):
                logger.warning(f"Skipping job without link: {job.get('title')}")
                continue

            # STICT FILTER: Check relevance before doing anything else
            if not self.is_relevant_job(job.get("title", "")):
                logger.debug(f"Skipping irrelevant job: {job.get('title')}")
                continue

            # Check if published recently
            pub_date_raw = job.get("published_at")
            if not self.is_published_today(pub_date_raw, days_window=self.days_window):
                logger.debug(f"Skipping job (too old): {job['title']} - {pub_date_raw}")
                continue

            # Ensure published_at is a datetime object for the database
            import pytz

            job["published_at"] = self.parse_date(pub_date_raw) or datetime.now(
                pytz.utc
            )

            # Refine description if it's too short (snippet)
            desc = job.get("description", "")
            is_markdown = False

            if not desc or len(desc) < 500:
                logger.info(f"Fetching full description for: {job['title']}")
                try:
                    full_desc, extracted_logo = await self.description_fetcher.fetch(
                        job["link"]
                    )
                    if full_desc:
                        job["description"] = full_desc
                        is_markdown = True
                        if extracted_logo:
                            if "company" not in job:
                                job["company"] = {}
                            if not job["company"].get("logo"):
                                job["company"]["logo"] = extracted_logo
                                logger.info(
                                    f"Updated company logo from description: {extracted_logo}"
                                )
                        logger.info("Successfully fetched full description")
                    else:
                        logger.warning(
                            "Could not fetch full description, keeping snippet"
                        )
                except Exception as e:
                    if job.get("source") == "RSS Feed":
                        print(
                            f"❌ ERROR IMPORTING RSS JOB: {job['title']} ({job['link']}) - {str(e)}"
                        )
                        logger.error(
                            f"Failed to fetch description for RSS job: {job['link']} - {str(e)}"
                        )
                        continue  # Skip this job as per user request to report failure
                    else:
                        logger.warning(
                            f"Could not fetch full description: {str(e)}, keeping snippet"
                        )

            # Ensure description is Markdown (if it was HTML)
            if job.get("description") and not is_markdown:
                # Check if it actually looks like HTML to avoid escaping plain text/markdown
                soup = BeautifulSoup(job["description"], "html.parser")
                if bool(soup.find()):
                    # Strip all images before converting to markdown
                    for img in soup.find_all("img"):
                        img.decompose()
                    job["description"] = md(str(soup))

            # 1. Deduplicate
            if self.deduplicator.is_duplicate(job):
                logger.debug(f"Skipping job (duplicate): {job['title']}")
                continue

            # 2. AI Categorize
            logger.info(f"Processing job: {job['title']}")
            ai_data = await self.categorizer.categorize_job(
                job["title"], job["description"]
            )

            if ai_data:
                if isinstance(ai_data.get("city"), list):
                    ai_data["city"] = (
                        str(ai_data["city"][0]) if ai_data["city"] else None
                    )
                elif ai_data.get("city") and not isinstance(
                    ai_data["city"], (str, type(None))
                ):
                    ai_data["city"] = str(ai_data["city"])

                # Ensure Salary fields are integers
                if ai_data.get("salary_min"):
                    try:
                        ai_data["salary_min"] = int(ai_data["salary_min"])
                    except (ValueError, TypeError):
                        ai_data["salary_min"] = None
                if ai_data.get("salary_max"):
                    try:
                        ai_data["salary_max"] = int(ai_data["salary_max"])
                    except (ValueError, TypeError):
                        ai_data["salary_max"] = None

                # Ensure remote is boolean (default False)
                ai_data["remote"] = bool(ai_data.get("remote", False))
                # Map is_remote to remote just in case LLM is stubborn
                if "is_remote" in ai_data:
                    ai_data["remote"] = bool(ai_data.pop("is_remote"))

                # Preserve original salary if available from scraper
                original_salary_min = job.get("salary_min")
                original_salary_max = job.get("salary_max")

                job.update(ai_data)

                # Restore original salary if it was present and strict
                if original_salary_min is not None:
                    job["salary_min"] = original_salary_min
                if original_salary_max is not None:
                    job["salary_max"] = original_salary_max

                # 3. Geocode and Location Handling
                # Ensure we have country and city if available from AI
                if ai_data.get("country"):
                    job["country"] = ai_data["country"]
                if ai_data.get("city"):
                    job["city"] = ai_data["city"]

                # Geocoding Logic
                geo_address = ai_data.get("formatted_address")

                # If no specific address, try to construct one from city + country
                if not geo_address and job.get("city"):
                    parts = [job["city"]]
                    if job.get("country"):
                        parts.append(job["country"])
                    geo_address = ", ".join(parts)

                if geo_address:
                    geo = self.geocoder.get_coordinates(geo_address)
                    if geo:
                        job["location_geo"] = {
                            "type": "Point",
                            "coordinates": [geo["lng"], geo["lat"]],
                        }
                        # Only overwrite formatted_address_verified if we actually got a specific result
                        job["formatted_address_verified"] = geo["formatted_address"]
                        # Also fill generic location if empty
                        if not job.get("location"):
                            job["location"] = geo["formatted_address"]

                # 4. Handle Company
                if job.get("company"):
                    company_id = self.db_client.upsert_company(job["company"])
                    job["company_id"] = company_id

                # 5. Handle Seniority
                if job.get("seniority"):
                    seniority_id = self.db_client.upsert_seniority(job["seniority"])
                    job["seniority_id"] = seniority_id

                # 6. Handle Employment Type (Explicit mapping if needed, though usually direct assignment)
                if ai_data.get("employment_type"):
                    job["employment_type"] = ai_data["employment_type"]

                # 7. Save Job
                inserted_id = self.db_client.insert_job(job)
                if inserted_id:
                    logger.info(
                        f"✅ IMPORTED: ID={inserted_id} | Title={job.get('title')} | Source={job.get('source')}"
                    )
                    print(
                        f"✅ IMPORTED: ID={inserted_id} | Title={job.get('title')} | Source={job.get('source')}"
                    )  # Console output as requested
                    lang_count += 1

                    # Update Statistics
                    self.stats[lang]["total"] += 1
                    src = job.get("source", "Unknown")
                    self.stats[lang]["sources"][src] = (
                        self.stats[lang]["sources"].get(src, 0) + 1
                    )
                else:
                    logger.info(
                        f"⏭️  SKIPPED (Duplicate/Error): Title={job.get('title')} | Source={job.get('source')}"
                    )
                    print(
                        f"⏭️  SKIPPED (Duplicate/Error): Title={job.get('title')} | Source={job.get('source')}"
                    )  # Console output as requested

            else:
                logger.warning(f"⚠️  AI Categorization Failed: Title={job.get('title')}")
                print(
                    f"⚠️  AI Categorization Failed: Title={job.get('title')}"
                )  # Console output

            # Rate limiting for AI API
            await asyncio.sleep(1)
        return lang_count

    async def run(self):
        logger.info(f"Starting job scraper run for languages: {self.languages}")

        for lang in self.languages:
            self.lang_count = 0
            # Initialize stats for this language
            if lang not in self.stats:
                self.stats[lang] = {"total": 0, "sources": {}}

            logger.info(f"Processing language: {lang}")

            for scraper in self.scrapers:
                if (
                    self.limit_per_language
                    and self.lang_count >= self.limit_per_language
                ):
                    logger.info(
                        f"Reached limit for {lang}, skipping remaining scrapers."
                    )
                    break

                # Special handling for Adzuna to do a broad search with pagination
                if isinstance(scraper, AdzunaScraper):
                    page = 1
                    while True:
                        if (
                            self.limit_per_language
                            and self.lang_count >= self.limit_per_language
                        ):
                            break

                        logger.info(
                            f"Scraping {scraper.__class__.__name__} page {page} for category 'it-jobs' in {lang}"
                        )
                        try:
                            jobs = await scraper.scrape(
                                lang=lang, category="it-jobs", page=page
                            )
                            if not jobs:
                                logger.info(
                                    "No more jobs found on this page, stopping Adzuna."
                                )
                                break

                            logger.info(f"Found {len(jobs)} potential jobs")
                            old_count = self.lang_count
                            self.lang_count = await self.process_job_list(
                                jobs, lang, self.lang_count
                            )

                            if self.lang_count == old_count:
                                logger.info(
                                    "No new jobs added from this page, might be all "
                                    "duplicates or too old. Trying one more page."
                                )
                                if page > 5:  # Safety break
                                    break

                            page += 1
                            await asyncio.sleep(2)  # Be nice to API
                        except Exception as e:
                            logger.error(f"Error in broad scraper: {e}")
                            break
                    continue

                for keyword in self.keywords:
                    if (
                        self.limit_per_language
                        and self.lang_count >= self.limit_per_language
                    ):
                        break

                    logger.info(
                        f"Scraping {scraper.__class__.__name__} for {keyword} in {lang}"
                    )
                    try:
                        jobs = await scraper.scrape(keyword, lang)
                        logger.info(f"Found {len(jobs)} potential jobs")
                        self.lang_count = await self.process_job_list(
                            jobs, lang, self.lang_count
                        )
                    except Exception as e:
                        logger.error(f"Error in keyword scraper loop: {e}")

        self.db_client.close()
        self.db_client.close()
        logger.info("Job scraper run finished.")
        self.print_report()

    def print_report(self):
        """Prints a summary report of the scraping run"""
        print("\n" + "=" * 60)
        print("📊  SCRAPER SUMMARY REPORT")
        print("=" * 60)

        total_all = 0
        for lang in sorted(self.stats.keys()):
            data = self.stats[lang]
            if data["total"] == 0:
                continue

            print(f"\n🌍 LANGUAGE: {lang.upper()}")
            print(f"   Total Imported: {data['total']}")
            print("   Breakdown by Source:")

            sorted_sources = sorted(
                data["sources"].items(), key=lambda x: x[1], reverse=True
            )
            for source, count in sorted_sources:
                print(f"   🔹 {source:<20}: {count}")

            total_all += data["total"]

        print("\n" + "-" * 60)
        print(f"🏆 GRAND TOTAL IMPORTED: {total_all}")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Scraper Orchestrator")
    parser.add_argument(
        "--languages",
        type=str,
        help="Comma-separated list of languages (e.g. it,en,es)",
    )
    parser.add_argument("--limit", type=int, help="Limit of total ads per language")
    parser.add_argument(
        "--days", type=int, default=1, help="Lookback window in days (default: 1)"
    )

    args = parser.parse_args()

    languages = args.languages.split(",") if args.languages else None

    orchestrator = JobScraperOrchestrator(
        languages=languages, limit_per_language=args.limit, days_window=args.days
    )
    asyncio.run(orchestrator.run())
