# MEMORY.md — DevBoards Import Service

## Last Updated
2026-05-01T10:45Z

## Project Status
claude-04 complete. Pre-filter + dedupe stages implemented and merged to feature/upgrade. 131 tests green. Next: claude-05 quality_gate + AI wiring (skills lexicon, replace OpenAI with Groq classifier).

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

### claude-03 — Base Infra (2026-05-01)

- **D-03-01**: `JobClassification` includes salary fields (salary_min, salary_max, currency). **Alt**: separate `JobSalary` only. **Rationale**: Groq extracts salary as part of classification; `classify_job() → JobClassification` must carry it. `Job.salary` (JobSalary) is populated by caller from classification output.
- **D-03-02**: Invalid AI enum values coerced to defaults in `_GroqOutput` via `field_validator(mode='before')`. **Alt**: raise ValidationError. **Rationale**: AI sometimes returns novel strings; hard fail would mark job AI_UNAVAILABLE when classification is otherwise usable.
- **D-03-03**: `ruff.toml` with `line-length=100` and per-file ignores for all legacy connectors/files. **Alt**: fix all legacy lint. **Rationale**: constraint "DO NOT touch existing connectors"; per-file ignores isolate legacy from new-code standards.
- **D-03-04**: Skills lexicon split (technical_skills vs skills) deferred to claude-05. **Alt**: implement now. **Rationale**: lexicon is a separate concern requiring its own SPEC and test coverage; placeholder comment left in `classify_job`.

### claude-01 — Architecture Decisions (2026-05-01)
Source: `docs/specs/00..04`. Format: Decision / Alternatives / Rationale.

#### Architecture (SPEC 00)
- **D-01-01**: Pipeline = Fetch → Normalize → Pre-filter → AI Classify (Groq) → Quality Gate → Persist; Expiration as separate job. **Alt**: keep monolithic main.py loop. **Rationale**: per-stage observability; pre-filter cuts AI cost; expiration cadence ≠ fetch cadence.
- **D-01-02**: New `pipeline/` package (normalize, prefilter, quality_gate, dedupe, expiration). **Alt**: place in `utils/` or `main.py`. **Rationale**: clear stage boundary; matches diagram 1:1; existing dirs (ai/, scrapers/, utils/, database/) have no semantic home for stage logic.
- **D-01-03**: `httpx` (sync+async) replaces `requests`+`aiohttp`. **Alt**: keep both. **Rationale**: one library, modern, integrates with `tenacity`.
- **D-01-04**: `pydantic-settings` for config. **Alt**: `os.getenv` ad-hoc. **Rationale**: SPEC mandate; typed, validated.
- **D-01-05**: `structlog` JSON logging with correlation IDs. **Alt**: stdlib logging. **Rationale**: SPEC mandate; contextvars binding.
- **D-01-06**: `tenacity` for retries. **Alt**: hand-rolled per scraper. **Rationale**: removes per-scraper inconsistency (P2-05).

#### Mongo Schema (SPEC 01)
- **D-01-07**: DB name = `itjobhub` (canonical). **Alt**: rename to `devboards`. **Rationale**: shared with Bun + News; rename = cross-repo coordination, low value. Update CLAUDE.md + .mcp.json instead.
- **D-01-08**: Rename `link` → `url`, `published_at` → `posted_at`. **Alt**: keep names. **Rationale**: standard nomenclature. 1-release transition: write both old+new names.
- **D-01-09**: `dedup_hash = sha1(title_normalized | company.name_normalized | source)`. **Alt**: cross-source hash; include posted_at. **Rationale**: cross-source hash collides distinct listings; posted_at exclusion enables re-post detection.
- **D-01-10**: Keep `seniorities` collection for legacy compat; deprecate post-migration. **Alt**: drop now. **Rationale**: Bun API may resolve `seniority_id`; defer.
- **D-01-11**: Unique indexes MANDATORY at boot. **Alt**: lazy. **Rationale**: P1-01 root cause; fail-loud.
- **D-01-12**: Double-write `link`+`url`, `published_at`+`posted_at` for 1 release. **Alt**: hard cutover. **Rationale**: scraper/Bun deploy on different cadences.

#### AI Classification (SPEC 02)
- **D-01-13**: Single Groq provider, no fallback. **Alt**: dual Groq+OpenAI. **Rationale**: simplicity; pipeline non-realtime; retry handles transient.
- **D-01-14**: 4000-char description truncation. **Alt**: token-based; full body. **Rationale**: deterministic; first 4000 chars carry classification signal.
- **D-01-15**: Pydantic validation on top of Groq JSON-mode. **Alt**: trust JSON-mode. **Rationale**: defense in depth (enum literals not enforced by JSON-mode).
- **D-01-16**: Skills lexicon split done locally (technical_skills vs skills). **Alt**: AI splits. **Rationale**: deterministic; lexicon updates without prompt changes.
- **D-01-17**: 3-attempt retry with exponential backoff. **Alt**: 5+. **Rationale**: Groq transient rates low; job re-enters next run.

#### Quality Gate (SPEC 03)
- **D-01-18**: 5-rule valid gate, first-match reject_reason. **Alt**: composite score. **Rationale**: clear root cause; matches SPEC requirement.
- **D-01-19**: Weighted-sum quality score (0.30 skills, 0.20 sen, 0.20 sal, 0.15 rem, 0.15 conf). **Alt**: equal weights. **Rationale**: skills drive perceived quality; salary deweighted (81.7% missing in current data).
- **D-01-20**: Persist rejected offers (don't drop). **Alt**: discard. **Rationale**: dedup_hash prevents AI re-run on junk; storage cost negligible.
- **D-01-21**: Premium requires (`clear_jd` OR `has_requirements`) AND NOT `boilerplate`. **Alt**: skip flags. **Rationale**: AI confidence misclassifies polished-empty boilerplate.
- **D-01-22**: `remote_mode in (hybrid, remote)` substitutes for missing salary in valid gate. **Alt**: salary required. **Rationale**: 81.7% missing salary today; collapsing yield <20% unacceptable.

#### Dedupe & Expiration (SPEC 04)
- **D-01-23**: 3 dedupe layers (URL, hash, fuzzy-flag). **Alt**: URL only; fuzzy-merge. **Rationale**: URL misses re-listings; fuzzy-merge risks false-positives; flag-only preserves data.
- **D-01-24**: Fuzzy threshold 92 / 14-day window. **Alt**: 85 / 30d. **Rationale**: tight threshold for first deploy; tune later.
- **D-01-25**: Expiration HEAD-only by default, GET-on-allow-list. **Alt**: GET always. **Rationale**: HEAD ~10× cheaper, politer.
- **D-01-26**: Dedupe hit does NOT re-run AI by default. **Alt**: re-run on hit. **Rationale**: AI is dominant cost; CLI `--reclassify` for override.
- **D-01-27**: Index creation fail-loud at boot. **Alt**: lazy/swallowed. **Rationale**: P1-01 must not recur.

## Pending Work

### Immediate (post-claude-04)
- **claude-04 — DONE**: `pipeline/language_detector.py`, `pipeline/prefilter.py`, `pipeline/dedupe.py`. 131 tests green.
- **claude-04 note**: `compute_dedup_hash` in models/job.py was fixed to lowercase source (SPEC 04 compliance).
- **claude-04 note**: `pipeline/language_detector.py` uses `from_all_languages()` (not subset) — detects unsupported langs as OTHER.
- **claude-05 — Skills lexicon + AI wiring**: Skills lexicon (technical vs soft split). Wire `ai/classifier.py` into pipeline. Replace OpenAI categorizer in main.py. Also: `pipeline/quality_gate.py` per SPEC 03.
- **claude-06 — Expiration job**: New `pipeline/expiration.py` per SPEC 04 §3. CLI sub-command `python main.py expire`.
- **claude-07 — Migration**: Backup → drop → re-index → first full run. Scripted in `docs/runbooks/migration-2026-05.md`.

### Cross-repo (out of this repo)
- **Bun API**: alias `link`↔`url`, `published_at`↔`posted_at`; accept `seniority` enum values `lead`, `principal`. Status: pending PR.
- **Qwik frontend**: accept `seniority=lead|principal` in `src/contexts/jobs.tsx`. Status: pending PR.
- **CLAUDE.md fix**: change "DB devboards" → "DB itjobhub" (top-level + apps/job_scraper). Status: documentation drift; do in claude-04 commit.
- **`.mcp.json` fix**: change connection string DB to `itjobhub`. Status: same.

### Open questions still pending answer (from discovery Q-01..Q-10)
- Q-04 wipe scope (jobs only? + companies? + seniorities?). Current SPEC 01 §5: jobs+companies wipe, seniorities preserved.
- Q-05 scheduling target (cron / systemd / Docker). Current SPEC 00: cron snippet in runbook only.
- Q-06 write path (direct Mongo vs Bun /jobs/import). Current: direct Mongo retained.
- Q-08 TechMap / JobsCollider: still disabled in main.py. Decide before claude-05.
- Q-09 salary policy: SPEC 03 mitigates by allowing remote_mode substitute; revisit after first run metrics.

### Watch-list (post-deploy)
- Calibrate quality gate after first run: distribution of `gate_reject_*`.
- Calibrate fuzzy-dup threshold (92) after first month: false-positive rate.
- Validate Groq cost estimate (~$6/month) against actual.
