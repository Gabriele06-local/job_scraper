# MEMORY.md — DevBoards Import Service

## Last Updated
2026-05-01

## Project Status
Discovery phase complete. 12 connectors, 2086 jobs in DB, 39.6% pass strict quality gate, no `link` unique index, scraper idle since Feb 2026.

## Architecture Snapshot
- Framework: requests + BeautifulSoup4 + feedparser + aiohttp (mixed sync/async)
- Orchestrator: `main.py` — single class `JobScraperOrchestrator` (546 LOC)
- AI: OpenAI GPT-4o-mini via `AsyncOpenAI` (model from `OPENAI_MODEL` env)
- DB: MongoDB local, db=`itjobhub` (NOT `devboards` as CLAUDE.md states)
- Scheduling: cron suggested in README only — no Dockerfile, no docker-compose, no systemd unit, no supervisor
- Deploy: manual `python3 main.py` invocation, venv-based
- Logging: stdlib `logging` to `job_scraper.log` (4.3 MB current). NOT structlog.

## Codebase Map
- `main.py` — orchestrator, sync iter scrapers × keywords × langs (546 LOC)
- `database/mongo_client.py` — pymongo client, upsert company/seniority, insert job (120 LOC)
- `ai/categorizer.py` — OpenAI single-call JSON extractor (54 LOC)
- `utils/deduplicator.py` — link-based dedup query (19 LOC)
- `utils/geocoding.py` — Google Maps geocode (40 LOC)
- `utils/description_fetcher.py` — async aiohttp HTML→Markdown (147 LOC)
- `scrapers/base_scraper.py` — abstract base + HTML normalizer (133 LOC)
- `scrapers/*.py` — 12 connectors (see Connectors Inventory)
- `tests/` — 5 test files, mostly per-scraper (jobisjob, reteinformaticalavoro)
- `verify_connection.py`, `verify_linkedin_import.py` — manual verification scripts
- `fix_dates.py`, `fix_cities.py` — one-shot data migration scripts

## Connectors Inventory

| Name | Type | URL/Endpoint | Auth | Status | Data quality |
|------|------|--------------|------|--------|--------------|
| LinkedIn | HTML | linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings | none | active (488 jobs) | brittle, 476 LOC |
| Adzuna | API | api.adzuna.com/v1/api/jobs | app_id+key | active (376) | OK |
| Jooble (jobleads) | API | {lang}.jooble.org/api | api_key | active (348) | SSL verify=False |
| JobisJob | HTML | jobisjob.{tld}/lavoro\|trabajo\|emploi\|arbeit\|jobs | none | active (251) | desc placeholder only |
| IProgrammatori | RSS | iprogrammatori.it/rss/offerte-lavoro-crawler.xml | none | active (118, IT only) | OK |
| Arbeitnow | API | arbeitnow.com/api/job-board-api | none | active (73) | unconditional sleeps |
| RSS (WeWorkRemotely, Himalayas, Remotive, Jobicy) | RSS | feeds | none | active (57) | company always "Unknown" |
| RemoteOK | API | remoteok.com/api | none | active (39) | OK |
| Jobicy | API | jobicy.com/api/v2/remote-jobs | none | minimal (1) | unconditional sleep |
| ReteInformaticaLavoro | HTML | reteinformaticalavoro.it/offerte-di-lavoro | none | active (0 in DB? need recheck) | regex-heavy (330 LOC) |
| TechMap | API | api.techmap.io/v1/jobs | bearer | DISABLED in main.py | spec incomplete |
| JobsCollider | RSS | jobscollider.com/remote-jobs.rss | optional | DISABLED in main.py | category fallback dead |

## MongoDB Current State

### Database & Collections
- DB: `itjobhub` (NOT `devboards`). Backend Bun/Elysia AND scraper write here.
- Collections (14): jobs, companies, seniorities, news, users, user_profiles, comments, likes, favorites, interactions, jobs_views, contacts, contact_replies, refresh_tokens
- DB size: 12.1 MB

### jobs collection
- Count: 2086 (incl. 333 test docs from Bun API import endpoints, NULL source/language/published_at)
- Storage: 15.4 MB
- Indexes: ONLY `_id_`. **`link` unique index NOT created** (Python `_ensure_indexes` silently fails)
- Schema: 31 fields (see docs/reports/00-discovery.md §3)

### companies / seniorities
- companies: 1645 docs. Indexes: only `_id_`. **`name` unique index NOT created**.
- seniorities: 20 docs (suspect duplicates of "Unknown", "Senior" etc). Indexes: `_id_` + `seniorities_level_key`.

### Quality stats (n=2086)
- description <200 chars: 333 (16%)
- no technical_skills: 804 (38.5%)
- no salary (min AND max): 1705 (81.7%)
- no published_at: 333 (16%)
- no company.name: 335 (16%)
- no location_geo: 1188 (57%)
- no seniority OR "Unknown": 1048 (50.2%)
- no language: 333 (16%)
- invalid link / no link: 0 / 0
- duplicate (title+company.name) groups: 88
- duplicate link groups: 0
- **pass strict gate**: 826 (39.6%)

Strict gate = desc≥200 AND skills≥1 AND has published_at AND has company.name AND has language AND seniority≠"Unknown" AND link starts http(s)://

### Temporal coverage
- 2026-01: 1374 jobs
- 2026-02: 379 jobs
- 2026-03..05: 0 jobs ← scraper idle ≥2.5 months
- null published_at: 333 (test data)

## Identified Issues

### P1 (blocking quality / production)
- P1-01: `_ensure_indexes` swallows IndexError silently. `link` unique on jobs and `name` unique on companies are MISSING in actual DB. Dedup relies on `find_one({link})` query (slow, racy).
- P1-02: 333 test docs in production jobs collection (`example.com`, `test.com` URLs from Bun API import smoke tests). Pollute counts and prevent strict-gate trust.
- P1-03: AI categorizer hardcodes OpenAI; SPEC mandates Groq with `GROQ_MODEL`. `GROQ_API_KEY` is in .env but unused.
- P1-04: No pre-filter before AI call. Every relevant title triggers OpenAI request → token waste on jobs that fail dedup later.
- P1-05: Jooble scraper has `verify=False` for SSL. Security regression.
- P1-06: 81.7% of jobs have no salary; 50.2% have no seniority; 57% have no geo. AI extraction quality is weak OR descriptions don't carry the data.

### P2 (quality / maintainability)
- P2-01: LinkedIn scraper 476 LOC HTML parsing — high maintenance cost, frequent breakage.
- P2-02: Bare `except:` blocks in arbeitnow_scraper.py:72, iprogrammatori_scraper.py:50.
- P2-03: `print()` calls in main.py alongside `logger.info` for "user-facing" messages — duplicates output, violates Python convention (no print).
- P2-04: No structlog. Plain stdlib logging, no JSON, no correlation IDs.
- P2-05: No retry/backoff abstraction. Each scraper handles errors differently (or not at all).
- P2-06: `scrapers × keywords × languages` triple-loop in main.py — 12 × 35 × 5 = ~2100 scraper invocations per run. Many redundant (e.g., RemoteOK is global, queried 35× per language).
- P2-07: AI categorizer uses no JSON Schema validation — relies on `response_format={type:json_object}` and trust.
- P2-08: No expiration job. Jobs never expire (`expires_at` field exists in Bun schema but scraper never sets it).
- P2-09: 88 duplicate (title+company) groups — same job from different sources not consolidated.
- P2-10: `lang_count` reset between scrapers but not between languages cleanly; subtle counting bug risk.
- P2-11: TechMap and JobsCollider commented-out in main.py — dead-or-future code.

### P3 (cosmetic / debt)
- P3-01: README mentions Apify for LinkedIn but actual code uses public LinkedIn `seeMoreJobPostings` endpoint. Doc stale.
- P3-02: CLAUDE.md says DB is `devboards` — actual DB is `itjobhub`. `.mcp.json` also points to `devboards`.
- P3-03: SUMMARY.md says ".husky0.1.0" — file content broken/concatenated.
- P3-04: `description: required` in Bun schema but scraper allows missing description (333 short_desc cases).
- P3-05: `seniorities` has 20 entries — likely contains noise variants ("Senior", "senior", "SENIOR"). Needs normalization.
- P3-06: Two `self.db_client.close()` calls in main.py:492-493 (duplicated).
- P3-07: Unused import `markdownify as md` in linkedin_scraper.py (per audit).
- P3-08: `fix_dates.py`, `fix_cities.py` are one-shot scripts left in root.
- P3-09: `job_scraper.log` is 4.3 MB and tracked? (Check .gitignore covers it — yes, `*.log`.)
- P3-10: Husky pre-commit only runs `bun run lint` and `bun run test` (Python via flake8/pytest in venv). No ruff yet despite SPEC.

## External Dependencies

### Bun + Elysia API (consumer)
- Repo: `/Users/alessandromicelli/Desktop/itjobhub/apps/backend`
- Job model: Prisma `schema.prisma:88-137`. 33 fields.
- Required by API import: `title`, `description`, `company.name`, `remote` (default false)
- Optional but consumed: skills[], technical_skills[], salary_min/max, seniority, link, source, language, published_at, location, location_geo, formatted_address, city, country, employment_type, requirements[], benefits[], experience_level
- Endpoints: GET/POST/PUT/DELETE /jobs, POST /jobs/import (single), POST /jobs/import/batch, /jobs/stats/skills, /jobs/match/batch, /jobs/:id/match, /jobs/:id/track, /jobs/:id
- **Scraper writes directly to MongoDB, NOT via API.** Bun import endpoints exist but are unused by scraper. Test data in DB came from these endpoints.

### Qwik frontend (downstream)
- Repo: `/Users/alessandromicelli/Desktop/itjobhub/apps/frontend`
- JobListing context: `src/contexts/jobs.tsx:26-58`
- Fields read by UI: title, company, companyLogo, description, skills, seniority (junior|mid|senior|unknown), availability (full_time|part_time|hybrid|contract|freelance|not_specified), location, location_geo, salary, externalLink (=link), publishDate (=published_at), language, remote, likes, dislikes, comments_count, views_count, clicks_count, user_reaction, is_favorite, companyScore
- Components: JobCard, JobHeader, JobDetail, FeaturedJobs

## Open Questions for User

1. Q-01: **Test data in prod**: 333 test docs (example.com/test.com URLs) — purge before re-import? Confirm.
2. Q-02: **DB name**: SPEC and CLAUDE.md say `devboards`. Actual DB is `itjobhub`. Which is canonical going forward?
3. Q-03: **AI provider migration**: Replace OpenAI categorizer with Groq (per SPEC)? Confirm OpenAI removal vs. dual-provider.
4. Q-04: **Wipe & re-import**: SPEC says "Wipe & re-import strategy approved." Wipe `jobs` collection entirely (and companies?) or keep historical and just upgrade pipeline forward?
5. Q-05: **Scheduling target**: cron, systemd, Docker container with cron, GitHub Actions, or external scheduler? Currently nothing.
6. Q-06: **API write path**: Should scraper switch to writing via Bun POST /jobs/import/batch (decouple DB schema) or continue direct Mongo writes?
7. Q-07: **Expiration**: separate job that HTTP-probes link and marks `expires_at`. Frequency? (daily? weekly?)
8. Q-08: **TechMap & JobsCollider**: keep disabled, complete integration, or delete?
9. Q-09: **Salary policy**: 81.7% missing — accept and stop estimating, or strengthen extraction?
10. Q-10: **Strict gate threshold**: 826/2086 = 39.6% pass. Acceptable, or raise threshold?

## Decision Log
(empty)

## Pending Work
(empty)
