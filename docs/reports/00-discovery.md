# Discovery Report — DevBoards Import Service

- Date: 2026-05-01
- Author: claude-opus-4-7
- Branch: feature/claude-00-discovery
- Scope: Python import service in `apps/job_scraper`. Read-only. No code changes.

---

## 1. Executive Summary

The import service is a synchronous orchestrator (with a thin async layer for description fetching and AI calls) that fans out 12 scrapers across 5 languages and ~35 keywords. All writes go directly to MongoDB at `itjobhub` (not `devboards` as docs claim). The scraper has been idle since Feb 2026 (today: 2026-05-01).

**Critical bugs**: (a) `link` unique index on `jobs` is silently absent — `_ensure_indexes` swallows the failure; (b) 333 test documents (example.com/test.com URLs) pollute the production `jobs` collection from Bun API smoke tests; (c) AI categorizer uses OpenAI although SPEC mandates Groq; (d) Jooble scraper disables SSL verification.

**Quality**: 39.6% of records pass a strict gate (description ≥200 chars, has skills, has published_at, has company.name, has language, seniority ≠ "Unknown", valid http(s) link).

---

## 2. Codebase Map

### Tree (relevant files only)

```
apps/job_scraper/
├── main.py                                   # Orchestrator (546 LOC)
├── ai/categorizer.py                         # OpenAI single-call (54 LOC)
├── database/mongo_client.py                  # pymongo client + upserts (120 LOC)
├── utils/
│   ├── deduplicator.py                       # Link-based dedup (19 LOC)
│   ├── description_fetcher.py                # aiohttp HTML→Markdown (147 LOC)
│   └── geocoding.py                          # Google Maps geocode (40 LOC)
├── scrapers/
│   ├── base_scraper.py                       # ABC + HTML normalizer (133 LOC)
│   ├── adzuna_scraper.py                     # API (79 LOC)
│   ├── arbeitnow_scraper.py                  # API (96 LOC)
│   ├── iprogrammatori_scraper.py             # RSS (84 LOC)
│   ├── jobicy_scraper.py                     # API (92 LOC)
│   ├── jobisjob_scraper.py                   # HTML (87 LOC)
│   ├── jobscollider_scraper.py               # RSS (96 LOC) [disabled]
│   ├── jooble_scraper.py                     # API (91 LOC)
│   ├── linkedin_scraper.py                   # HTML (476 LOC)
│   ├── remoteok_scraper.py                   # API (66 LOC)
│   ├── reteinformaticalavoro_scraper.py      # HTML (330 LOC)
│   ├── rss_scraper.py                        # RSS (61 LOC)
│   └── techmap_scraper.py                    # API (99 LOC) [disabled]
├── tests/                                    # pytest tests (5 files)
├── verify_connection.py                      # Manual MongoDB ping
├── verify_linkedin_import.py                 # Manual LinkedIn run
├── fix_dates.py                              # Migration: normalize dates
├── fix_cities.py                             # Migration: normalize cities
├── .env.example                              # 11 vars
├── .husky/pre-commit                         # bun run lint + test
├── package.json                              # flake8 + pytest via venv
├── requirements.txt                          # 14 packages
└── README.md / SUMMARY.md / VERSION (0.1.0)
```

### Per-file purpose & debt

| File | Purpose | Debt |
|------|---------|------|
| main.py | Orchestrator, scrapers × keywords × languages loop, AI call, geocode, save | scrapers × 35 keywords × 5 langs = ~2100 invocations/run; double `db_client.close()`; print() + logger duplicated |
| ai/categorizer.py | OpenAI Chat Completions JSON extractor | hardcoded OpenAI; no schema validation; prompt truncates desc at 3000 chars |
| database/mongo_client.py | pymongo wrapper, upsert company/seniority, insert job | `_ensure_indexes` swallows IndexError silently — UNIQUE indexes NOT applied; uses `datetime.utcnow()` (deprecated in 3.12) |
| utils/deduplicator.py | `find_one({link})` check | no DB-level guarantee; race conditions possible; full collection scan (no index) |
| utils/geocoding.py | Google Maps geocode v1 | sync `requests`; no retry; no caching → ~$N/run |
| utils/description_fetcher.py | Heuristic content extraction from job URL | brittle CSS selectors; no robots.txt check; verify=False (ssl=False) |
| scrapers/base_scraper.py | ABC + HTML hierarchy normalizer (h1→h2, level clamp) | normalizer only used by some scrapers |

### Framework
- **Mixed**: requests (sync HTTP), aiohttp (async description fetch), feedparser (RSS), BeautifulSoup4 + lxml (HTML), pymongo (MongoDB), openai (AsyncOpenAI), googlemaps via raw requests
- **Not used**: structlog, httpx, Pydantic, Pydantic Settings, scrapy
- Async sparsely applied — `process_job_list` is async but most scrapers are sync, wrapped in `async def scrape()` that calls sync code

### Scheduling
- **None in repo**: no Dockerfile, no docker-compose, no systemd unit, no supervisor, no GitHub Actions cron
- README suggests cron line: `0 * * * * /path/to/venv/bin/python3 /full/path/to/main.py --limit 50 --days 1`
- Husky pre-commit invokes `bun run lint` + `bun run test`
- `package.json` defines `lint` (flake8) and `test` (pytest) via local venv

### Config & secrets
- `.env.example` documents: DATABASE_URL, MONGO_DB, OPENAI_API_KEY, OPENAI_MODEL, GROQ_API_KEY, GOOGLE_MAPS_API_KEY, ADZUNA_APP_ID/KEY, JOOBLE_API_KEY, APIFY_API_TOKEN, MAX_JOBS_PER_SOURCE, SCRAPING_DELAY, TECHMAP_API_TOKEN, JOBSCOLLIDER_API_TOKEN
- `MAX_JOBS_PER_SOURCE` and `SCRAPING_DELAY` declared but not consumed in code (verified absent in scrapers / main)
- `APIFY_API_TOKEN` declared but LinkedIn scraper uses public endpoint, not Apify (README inconsistency)
- Real `.env` not opened (per instructions)

---

## 3. Connectors Inventory

### Output schema (union of fields produced by scrapers)
Common: `title`, `company.name`, `company.logo` (optional), `description`, `link`, `source`, `original_language`, `published_at`, `location_raw`, `remote`
Variable: `salary_min`, `salary_max`, `currency`, `employment_type`, `external_id`

### Per-connector mapping

| Connector | Type | Endpoint / URL | Auth | Key Output Fields | LOC | DB count |
|-----------|------|----------------|------|-------------------|-----|----------|
| LinkedIn | HTML | linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search | none | +external_id, +remote | 476 | 488 |
| Adzuna | API | api.adzuna.com/v1/api/jobs/{country}/search/{page} | app_id+key | +salary_min/max | 79 | 376 |
| Jooble | API | {lang}.jooble.org/api/{api_key} | api_key | base | 91 | 350 |
| JobisJob | HTML | jobisjob.{tld}/lavoro\|trabajo\|emploi\|arbeit\|jobs | none | desc=placeholder | 87 | 251 |
| IProgrammatori | RSS | iprogrammatori.it/rss/offerte-lavoro-crawler.xml | none | +remote | 84 | 118 |
| Arbeitnow | API | arbeitnow.com/api/job-board-api | none | +remote | 96 | 73 |
| RSS (mixed) | RSS | WeWorkRemotely, Himalayas, Remotive, Jobicy | none | company="Unknown" | 61 | 57 |
| RemoteOK | API | remoteok.com/api | none | +remote | 66 | 39 |
| ReteInformaticaLavoro | HTML | reteinformaticalavoro.it/offerte-di-lavoro | none | +salary_min/max +employment_type | 330 | 0 |
| Jobicy | API | jobicy.com/api/v2/remote-jobs | none | +salary_min/max +employment_type | 92 | 1 |
| TechMap | API | api.techmap.io/v1/jobs | bearer | +salary +currency | 99 | disabled |
| JobsCollider | RSS | jobscollider.com/remote-jobs.rss | optional | +salary +currency | 96 | disabled |

### Connector-level debt highlights
- **LinkedIn (P2)**: 476 LOC, brittle CSS, multilingual noise filters (regex), markdownify import declared inside method.
- **Jooble (P1)**: SSL `verify=False` + `urllib3` warnings suppressed.
- **JobisJob (P2)**: description = placeholder string, full HTML never fetched in scraper itself (relies on `DescriptionFetcher` later, ad-hoc).
- **ReteInformaticaLavoro (P3)**: prepends extracted skills to description for AI context — couples scraper to downstream.
- **Bare excepts**: arbeitnow_scraper.py:72, iprogrammatori_scraper.py:50.
- **Sleeps**: jobicy unconditionally `time.sleep(1)` per request; arbeitnow always sleeps 5s before retry attempts.

### Frequency / cadence
- No scheduler enforces frequency. Manual invocation. Default `--days 1` (today + yesterday).
- Per-run keyword loop = `len(keywords)=35` × `len(scrapers)=10 active` × `len(languages)=5` = 1750 scraper.scrape() calls (Adzuna handled separately, paginated).
- AI rate-limit: hardcoded `await asyncio.sleep(1)` between every job in `process_job_list`.

### Dedup
- **Application-level**: `JobDeduplicator.is_duplicate(job)` runs `db.jobs.find_one({"link": link})` per job. No `link` index → full scan. O(N) per job.
- **DB-level**: `_ensure_indexes` *attempts* `create_index([("link", 1)], unique=True)` but except branch logs a warning and continues. Actual `jobs` indexes: only `_id_`. Verified.
- **Title+company duplicates**: 88 groups have `count > 1` for `(title, company.name)`. Not detected/consolidated.

### Tagging
- **Language**: from AI `language` field (often falls back to `original_language` set by scraper).
- **Skills**: from AI `technical_skills`. 38.5% empty.
- **Seniority**: from AI `seniority`, upserted into `seniorities` collection. 50.2% missing or "Unknown".
- **Location**: AI extracts `formatted_address` / `city` / `country`; geocoded only if non-empty.

---

## 4. MongoDB Current State

### Server
- URI: `mongodb://localhost:27017` (replicaSet=rs0 in .env.example)
- Active DB: `itjobhub` — confirmed via `list-databases`. `devboards` does NOT exist.
- 5 databases total: admin, config, itjobhub, local, scout_iq

### Collections in `itjobhub`
14 collections: jobs, companies, seniorities, news, users, user_profiles, comments, likes, favorites, interactions, jobs_views, contacts, contact_replies, refresh_tokens

### `jobs` collection

#### Counts & size
- Document count: **2086**
- Storage size: 15.4 MB
- Average doc size: 3.9 KB

#### Schema (sample 100, 31 fields)
```
_id              ObjectId
title            String
company          Document { name: String, logo: String|null }
description      String
link             String
location_raw     String
source           String
original_language String
published_at     Date
external_id      String          (LinkedIn only)
remote           Boolean
language         String
technical_skills Array<String>
requirements     Array<String>
benefits         Array<String>
salary_min       Number|null
salary_max       Number|null
seniority        String
employment_type  String
formatted_address String|null
city             String|null
country          String|null
location_geo     Document { type: String, coordinates: [Number] }
formatted_address_verified String
location         String
company_id       ObjectId
seniority_id     ObjectId
created_at       Date
clicks_count     Number|null
views_count      Number|null
updated_at       Date
```

#### Indexes
- `_id_` (default) **only**
- **MISSING (silently)**: `link` unique
- No index on: `company_id`, `seniority_id`, `published_at`, `language`, `source`, `location_geo` (no 2dsphere)

#### Quality stats
| Metric | Count | % |
|--------|------:|--:|
| Total | 2086 | 100% |
| description <200 chars | 333 | 16.0% |
| no technical_skills | 804 | 38.5% |
| no salary (min AND max null) | 1705 | 81.7% |
| no published_at | 333 | 16.0% |
| no company.name | 335 | 16.1% |
| no location_geo | 1188 | 56.9% |
| no seniority OR "Unknown" | 1048 | 50.2% |
| no language | 333 | 16.0% |
| invalid link | 0 | 0% |
| no link | 0 | 0% |

#### Strict gate (intersection)
desc≥200 ∧ skills≥1 ∧ has published_at ∧ has company.name ∧ has language ∧ seniority≠"Unknown" ∧ link starts http(s)://

**Pass: 826 / 2086 = 39.6%**

#### Duplicate analysis
- (title, company.name) duplicate groups (count >1): **88**
- (link) duplicate groups: **0** — currently clean by chance, not by index

#### Source breakdown
| Source | Count |
|--------|------:|
| LinkedIn | 488 |
| Adzuna | 376 |
| Jooble (jobleads.com) | 348 |
| **null (test data)** | **333** |
| JobisJob | 251 |
| IProgrammatori | 118 |
| Arbeitnow | 73 |
| RSS Feed | 57 |
| RemoteOK | 39 |
| Jooble (workable.com) | 2 |
| Jobicy | 1 |

#### Language breakdown
| Language | Count |
|----------|------:|
| en | 673 |
| it | 651 |
| **null (test data)** | **333** |
| fr | 167 |
| es | 140 |
| de | 117 |
| pt | 3 |
| kn | 1 |
| nl | 1 |

#### Temporal (by published_at month)
- 2026-01: 1374 jobs
- 2026-02: 379 jobs
- 2026-03 → 2026-05: **0 jobs** ← scraper has not produced output for ~2.5 months
- null published_at: 333 (test data)

### Test-data contamination
333 documents share these traits: `source=null`, `language=null`, `published_at=null`, titles like "Batch Job 1", "Batch Job 2", "Senior Fullstack Dev", "Imported Job", links matching `https://example.com/...` and `https://test.com/...`. These came from Bun API smoke tests against POST /jobs/import and /jobs/import/batch. They inflate every "missing field" metric by exactly 333.

#### Quality stats — adjusted for test data (n_real = 1753)
| Metric | Real % |
|--------|------:|
| description <200 | ~0% |
| no technical_skills | (804-333)/1753 ≈ 26.9% |
| no salary | (1705-333)/1753 ≈ 78.3% (still high) |
| no company.name | ~0.1% |
| no location_geo | (1188-333)/1753 ≈ 48.8% |
| no seniority/Unknown | (1048-333)/1753 ≈ 40.8% |

### `companies` collection
- Count: 1645
- Indexes: only `_id_`. **MISSING `name` unique** (Python claims to create it).
- Suspected duplicates by name normalization (not measured here).

### `seniorities` collection
- Count: 20 (suspect — should be ~5: Junior, Mid, Senior, Lead, Unknown)
- Indexes: `_id_`, `seniorities_level_key` on `level`
- Likely contains case/whitespace variants — needs normalization audit

---

## 5. External Dependencies

### Bun + Elysia API (consumer)
- Path: `apps/backend`
- Job model: `prisma/schema.prisma:88-137` (33 fields)
- API routes: `src/routes/jobs.ts`
- Required for import: `title`, `description`, `company.name`, `remote` (default false)
- Optional consumed: skills[], technical_skills[], salary_min/max, seniority, link, source, language, published_at, location, location_geo, formatted_address, city, country, employment_type, requirements[], benefits[], experience_level, expires_at, status
- Compute fields (API-managed, not from scraper): views_count, clicks_count, status, created_at, updated_at
- Endpoints touched by scraper today: **none**. Scraper writes directly to Mongo. POST /jobs/import and /jobs/import/batch exist but are unused.

### Qwik frontend (downstream)
- Path: `apps/frontend`
- Read fields (from `src/contexts/jobs.tsx:26-58`): id, title, company, companyLogo, description, skills, seniority (junior|mid|senior|unknown), availability (full_time|part_time|hybrid|contract|freelance|not_specified), location, location_geo, salary, externalLink, publishDate, language, remote, comments_count, views_count, clicks_count, user_reaction, is_favorite, companyScore, companyLikes, companyDislikes
- Components: JobCard, JobHeader, JobDetail, FeaturedJobs

### News scraper (`apps/news_scraper`)
- Sibling Python service. Out of scope but uses the same `itjobhub` DB (`news` collection, 14 collections incl. `news`).

---

## 6. Issues — Prioritized

### P1 — blocking quality / production
1. `_ensure_indexes` silently fails. `jobs.link` and `companies.name` unique indexes do not exist. Dedup is application-only and racy.
2. 333 test docs contaminate `jobs` collection (example.com/test.com).
3. AI categorizer uses OpenAI; SPEC requires Groq (`GROQ_MODEL`, default `llama-3.1-8b-instant`).
4. No pre-filter before AI call. AI fires for every relevant title even before dedup → token cost.
5. `Jooble` scraper uses `verify=False` for SSL.
6. 81.7% of records have no salary; 50.2% have no seniority. Pipeline quality below SPEC strict-gate intent.

### P2 — quality / maintainability
1. LinkedIn scraper 476 LOC HTML parsing — high maintenance burden.
2. Bare `except:` in arbeitnow_scraper.py:72, iprogrammatori_scraper.py:50.
3. `print()` in main.py alongside `logger.info` — duplicate outputs, violates Python conventions.
4. No structlog. Plain stdlib logging, no JSON, no correlation IDs.
5. No retry/backoff abstraction; per-scraper inconsistency.
6. Scrapers × keywords × languages triple-loop produces ~1750 scrape calls/run; many redundant (RemoteOK is global).
7. No JSON Schema validation on AI response.
8. No expiration job. `expires_at` never set.
9. 88 (title, company) duplicate groups — same job from different sources not consolidated.
10. `lang_count` reset hygiene in main.py — subtle counting bug risk.
11. TechMap and JobsCollider commented-out — keep / complete / delete unclear.

### P3 — cosmetic / debt
1. README mentions Apify; code uses public LinkedIn endpoint.
2. CLAUDE.md says DB is `devboards`; actual is `itjobhub`. `.mcp.json` also `devboards`.
3. SUMMARY.md content broken (".husky0.1.0").
4. 333 short_desc entries — Bun schema requires `description`. Inconsistency must be reconciled.
5. `seniorities` has 20 entries; expect ~5 — needs normalization.
6. `db_client.close()` called twice in main.py:492-493.
7. `markdownify` import declared inside `linkedin_scraper.py:369` method.
8. `fix_dates.py`, `fix_cities.py` — one-shot scripts left in root.
9. `job_scraper.log` 4.3 MB on disk (gitignored).
10. Husky pre-commit runs `bun run lint` (flake8) + `pytest`. SPEC mandates `ruff` — not yet wired.

---

## 7. Open Questions

1. Test data: confirm purge of 333 test docs.
2. DB name: `devboards` (CLAUDE.md, .mcp.json) vs `itjobhub` (actual). Canonical?
3. AI provider: full Groq migration vs dual.
4. Wipe & re-import: full wipe (jobs + companies?) or forward-only.
5. Scheduling: cron / systemd / Docker / GitHub Actions / external?
6. Write path: continue direct Mongo write, or POST /jobs/import/batch?
7. Expiration job cadence (daily? weekly?).
8. TechMap / JobsCollider: keep, finish, or delete?
9. Salary policy: accept high missingness or strengthen extraction?
10. Strict gate threshold target.

---

## 8. Out-of-Scope (recorded only)
- Bun + Elysia API source mods.
- Qwik frontend mods.
- `news_scraper` sibling service.
- DevBoards business logic outside import.
