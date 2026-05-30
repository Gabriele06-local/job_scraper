# MEMORY.md — DevBoards Import Service

## Last Updated
2026-05-30T00:00Z — multi-model AI pipeline (SPEC 05) shipped to develop.

## Project Status
STABLE — v1 implemented. All pipeline stages shipped (claude-03 through claude-09). 263 tests green. Operational docs complete. Migration script ready at `scripts/migrate.py`. Execute manually with `--confirm` AFTER:
- Backup verified (`mongodump`)
- Bun API in read-only mode
- User has reviewed dry-run output (`docs/reports/03-migration-dryrun.md`)

## Architecture Snapshot
- Framework: requests + BeautifulSoup4 + feedparser + aiohttp (mixed sync/async)
- Orchestrator: `main.py` — single class `JobScraperOrchestrator` (546 LOC)
- AI: OpenAI GPT-4o-mini via `AsyncOpenAI` (model from `OPENAI_MODEL` env)
- DB: MongoDB local, db=`itjobhub` (NOT `devboards` as CLAUDE.md states)
- Scheduling: `Dockerfile` + `docker-compose.yml` added (importer/expirer as sleep-loop services). Cron alternative in `docs/runbooks/cron.example`.
- Deploy: manual `python3 main.py` invocation, venv-based
- Logging: stdlib `logging` to `job_scraper.log` (4.3 MB current). NOT structlog.

## Codebase Map
- `main.py` — legacy orchestrator, sync iter scrapers × keywords × langs (546 LOC)
- `connectors/` — **NEW**: BaseConnector ABC + 12 connector adapters + registry
- `connectors/__init__.py` — REGISTRY + get_enabled_connectors()
- `connectors/base.py` — BaseConnector ABC + SourceType enum
- `database/mongo_client.py` — pymongo client, upsert company/seniority, insert job (120 LOC)
- `ai/categorizer.py` — OpenAI single-call JSON extractor (54 LOC)
- `utils/deduplicator.py` — link-based dedup query (19 LOC)
- `utils/geocoding.py` — Google Maps geocode (40 LOC)
- `utils/description_fetcher.py` — async aiohttp HTML→Markdown (147 LOC)
- `scrapers/base_scraper.py` — abstract base + HTML normalizer (133 LOC)
- `scrapers/*.py` — 12 legacy connectors (wrapped by connectors/, unchanged)
- `tests/` — test files inc. test_connectors_smoke.py (21 new tests)
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

### claude-05..12 — Multi-Model AI Pipeline (2026-05-30)

See `docs/specs/05-multi-model-ai-pipeline.spec.md` + `docs/reports/05-multi-model-ai-summary.md`.

- **D-05-1**: EXTRACT default tier = FAST (8b), escalate once to STRUCT (qwen-32b) on confidence < 0.7. **Alt**: 32b on every job per brief; +TRIAGE pre-screen. **Rationale**: cost goal dominates; 8b adequate for modal clean posting; escalation buys 32b accuracy only where needed. Owner-confirmed.
- **D-05-2/3**: Embeddings deferred (provider-agnostic interface only); search relevance via lexical retrieve → 70b rerank, not vectors. **Rationale**: Groq has no embeddings + Mongo is self-hosted (no Atlas Vector Search). Owner-confirmed.
- **D-05-5**: Split provider / router / prompts / cache / telemetry; provider is the only Groq touch-point, router the only place with model names. **Rationale**: provider-agnostic, reused in backend, isolates the SDK.
- **D-05-7**: AI cache keyed by sha256(task|prompt_version|input) — model NOT in key — so re-imports reuse the final (post-escalation) result with zero calls.
- Failures exhaust 3 within-tier retries and return None (no escalation) — escalation is only for successful-but-low-confidence results.
- Legacy `classify_job` kept on the raw client (deprecated): its tests assert raw `groq.RateLimitError` propagation, which the provider's error translation would mask.

### claude-06 — Connectors Refactor (2026-05-01)

- **D-06-01**: Adapter pattern (connectors/ wraps scrapers/) over in-place refactor. **Alt**: modify existing scraper classes directly. **Rationale**: scrapers/ already have ruff per-file ignores and working logic; adapter preserves both without touching extraction code.
- **D-06-02**: `fetch() -> Iterator[dict]` wraps `asyncio.run(scraper.scrape())` in sync context. **Alt**: convert scrapers to sync. **Rationale**: scrapers are async in name only (use `requests`); asyncio.run() is safe in sync pipeline context without event loop.
- **D-06-03**: Global connectors (Arbeitnow, RemoteOK, Jobicy) call `scrape(keyword="", lang="en")` once. **Alt**: iterate per keyword. **Rationale**: they do client-side filtering; `"" in str` is always True so empty keyword = all jobs; one HTTP call is more polite.
- **D-06-04**: Keywords/languages stored in Settings with env-override support. **Alt**: hardcode per connector. **Rationale**: SPEC 00 says config via env; Settings.scrape_keywords/scrape_languages added with main.py defaults.
- **D-06-05**: Smoke tests use mocked `requests.get`. **Alt**: real network calls. **Rationale**: CI reliability; real-network smoke is a manual script concern.

### claude-05 — AI Classifier + Quality Gate + Pipeline (2026-05-01)

- **D-05-01**: Seniority accuracy 67.9% on ground truth (29 offers, llama-3.1-8b-instant). **Alt**: accept or re-test. **Rationale**: does NOT block merge per task spec; flag for prompt tuning in claude-06+. Role family 89.3%, skills P/R ~74% both acceptable. Likely root cause: small fixtures set (29); model conflates junior/mid for ambiguous postings.
- **D-05-02**: `classify(job_raw: dict)` added to `GroqClassifier` as main interface; `classify_job(text: str)` kept for backward compat (used by existing tests). **Alt**: migrate all callers. **Rationale**: breaking existing tests adds no value; new interface is cleaner per SPEC 02 §4.
- **D-05-03**: `_single_call` unwraps `[{...}]` list-wrapped JSON responses. **Alt**: treat as validation error → retry. **Rationale**: observed in baseline run (Security Engineer fixture); deterministic fix avoids burning 3 retries on a known pattern.
- **D-05-04**: `ImportPipeline.run(raw_jobs)` takes `list[RawJob]` (not iterator). **Alt**: accept iterator. **Rationale**: counter needs `total` upfront; list allows chunked batching by caller if needed.
- **D-05-05**: Prefilter-rejected jobs persisted to MongoDB (dedup_hash stored). **Alt**: discard entirely. **Rationale**: prevents AI re-run on same junk on next import run; matches D-01-20 principle.

### claude-03 — Base Infra (2026-05-01)

- **D-03-01**: `JobClassification` includes salary fields (salary_min, salary_max, currency). **Alt**: separate `JobSalary` only. **Rationale**: Groq extracts salary as part of classification; `classify_job() → JobClassification` must carry it. `Job.salary` (JobSalary) is populated by caller from classification output.
- **D-03-02**: Invalid AI enum values coerced to defaults in `_GroqOutput` via `field_validator(mode='before')`. **Alt**: raise ValidationError. **Rationale**: AI sometimes returns novel strings; hard fail would mark job AI_UNAVAILABLE when classification is otherwise usable.
- **D-03-03**: `ruff.toml` with `line-length=100` and per-file ignores for all legacy connectors/files. **Alt**: fix all legacy lint. **Rationale**: constraint "DO NOT touch existing connectors"; per-file ignores isolate legacy from new-code standards.
- **D-03-04**: Skills lexicon split (technical_skills vs skills). Implemented in claude-06+ (`utils/skills_lexicon.py` — ~300-entry curated set, `split_skills()` called in `classifier.py`). Quality gate `_MIN_VALID_SKILLS` bumped from 1 to 2 post-split. **Alt**: keep deferred. **Rationale**: SPEC 03 already mandated >=2 post-split; deferral was temporary.

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

### claude-07 — Expiration Checker + CLI Scheduler (2026-05-01)

- **D-07-01**: `import_service/` namespace package for CLI instead of top-level `cli.py`. **Alt**: root-level cli.py. **Rationale**: task spec requires `python -m import_service.cli`; namespace package avoids touching legacy `main.py`.
- **D-07-02**: docker-compose uses sleep-loop (no cron daemon in container). **Alt**: `supercronic` or system cron. **Rationale**: minimal image; intervals configurable via env vars without rebuilding. `docs/runbooks/cron.example` covers the host-cron alternative.
- **D-07-03**: max_age jobs force-expired without probe (posted_at < cutoff AND never probed). **Alt**: probe them too. **Rationale**: SPEC 04 §3.4; 60+ day old never-probed listings are certainly dead; avoids wasted HTTP calls.
- **D-07-04**: `_DomainLimiter` asyncio.Lock for per-host rate limit. **Alt**: httpx `Limits(max_connections_per_host)`. **Rationale**: `httpx.Limits` has no per-host param in current version; Lock + timestamp enforces 1 req/s per host correctly.

### claude-09 — Operational Docs (2026-05-01)

- **D-09-01**: `README.md` fully rewritten from legacy (Apify/OpenAI references removed, CLI + Docker documented). **Alt**: patch incrementally. **Rationale**: legacy README referenced removed features (Apify, OpenAI); a full rewrite is cleaner and prevents confusion.
- **D-09-02**: `.env.example` expanded to cover all `config.py` fields with inline comments. **Alt**: keep minimal. **Rationale**: first-run operator needs to know which vars are mandatory vs. optional without reading source.
- **D-09-03**: `CHANGELOG.md` created at v1.0.0 grouping all claude-03..09 changes. **Alt**: per-feature changelogs. **Rationale**: single file easier to scan; v1.0.0 semantic marks the pipeline as stable.
- **D-09-04**: `docs/runbooks/cost-monitoring.md` includes model-upgrade path (llama-3.1-8b-instant → llama-3.3-70b-versatile). **Rationale**: seniority accuracy 67.9% < 80%; operator should know cost impact before upgrading.

### claude-10 — Prompt Tuning + Skills Lexicon (2026-05-26)

- **D-10-01**: Seniority system prompt rules added (`_SYSTEM_PROMPT`). **Alt**: few-shot examples; schema descriptions. **Rationale**: minimal token bloat (~200 chars); title-as-primary-signal rule directly addresses 6/9 errors (mid→senior over-classification).
- **D-10-02**: `utils/skills_lexicon.py` created (~300-entry curated set). `split_skills()` called in `classifier.py` post-Groq. **Alt**: AI splits. **Rationale**: deterministic; lexicon updates without prompt changes; already decided at D-01-16.
- **D-10-03**: Quality gate `_MIN_VALID_SKILLS` bumped from 1 → 2 post-lexicon-split. **Alt**: keep 1. **Rationale**: SPEC 03 §2.1 requires >=2; temporary deferral (D-03-04) is resolved.
- **D-10-04**: `cv_drop_score` (0..1) added to Groq schema, `_GroqOutput`, `JobClassification`, and `compute_quality_score` (weight 10%, taken from skills 25→20 and seniority 15→10). **Alt**: separate AI call. **Rationale**: single Groq call avoids extra cost; CV drop is a holistic posting-quality signal complementary to existing dimensions.

### claude-08 — Migration Runbook + Wipe-and-Reimport Script (2026-05-01)

- **D-08-01**: `--confirm` is the destructive gate; absence (or `--dry-run`) keeps the script side-effect-free. **Alt**: `--yes-i-really-mean-it`-style boolean / two-step prompt. **Rationale**: explicit flag survives non-interactive shells (cron, CI) while making accidental destruction impossible without the explicit token.
- **D-08-02**: Backup is operator-driven (manual `mongodump` per runbook), NOT performed by `migrate.py`. **Alt**: script invokes `mongodump`. **Rationale**: backup destination, retention, and credentials vary per environment; embedding fragile shell-out couples the script to operator infra.
- **D-08-03**: Bun read-only flip is documented as a manual operator step. **Alt**: script triggers it via API. **Rationale**: cross-repo coupling; Bun deployment + auth model is out of scope for this repo.
- **D-08-04**: Idempotent drop — skip when collection already empty; rely on dedup_hash upsert in pipeline persistence to make the re-import safely re-runnable. **Alt**: refuse to re-run after partial failure. **Rationale**: SPEC 04 dedupe contract already guarantees idempotency; an opinionated guard would block legitimate resumes.
- **D-08-05**: Cost estimate uses baseline-derived avg `$0.0000533` per Groq call × pre-filter survivors. **Alt**: per-offer token estimate from description length. **Rationale**: baseline is empirical and stable; per-offer tokenization adds complexity for an upper-bound estimate.

## Connector Interface (claude-06)

### fetch() Contract
- `BaseConnector.fetch() -> Iterator[dict]` — yields raw job dicts
- Each dict has: `title`, `company`, `link`, `description`, `source`, `original_language`, `published_at`, `location_raw` (fields vary by source)
- Global connectors (Arbeitnow, RemoteOK, Jobicy): one HTTP call, yield all
- Keyword-aware connectors (Adzuna, LinkedIn, Jooble, JobisJob): iterate over `settings.scrape_keywords × settings.scrape_languages`
- IT-only connectors (IProgrammatori, ReteInformaticaLavoro): fixed `lang="it"`

### Registry
- `connectors.REGISTRY` — `dict[str, ConnectorEntry]`
- `get_enabled_connectors()` — returns instantiated enabled connectors
- Runtime disable: set `DISABLED_CONNECTORS=linkedin,jooble` env var
- TechMap: disabled (API spec incomplete)
- JobsCollider: disabled (category feed 404)

### Per-Connector Status (2026-05-01)

| Connector | Type | Enabled | Notes |
|---|---|---|---|
| LinkedIn | html | yes | Brittle (476 LOC), high breakage risk |
| Adzuna | api | yes | Needs ADZUNA_APP_ID + ADZUNA_APP_KEY |
| Jooble | api | yes | SSL verify=False (P1-05), needs JOOBLE_API_KEY |
| JobisJob | html | yes | Description placeholder only |
| IProgrammatori | rss | yes | IT-only, good quality |
| Arbeitnow | api | yes | sleep(5) unconditional |
| RemoteOK | api | yes | No auth, global remote |
| Jobicy | api | yes | sleep(1) unconditional, low yield |
| ReteInformaticaLavoro | html | yes | IT-only, 330 LOC regex |
| RSS | rss | yes | EN feeds only |
| TechMap | api | **no** | API spec not verified |
| JobsCollider | rss | **no** | Category feed 404 |

Full issue list: `docs/reports/02-connectors-status.md`

## Pending Work

### Immediate (post-claude-05)
- **claude-04 — DONE**: `pipeline/language_detector.py`, `pipeline/prefilter.py`, `pipeline/dedupe.py`. 131 tests green.
- **claude-04 note**: `compute_dedup_hash` in models/job.py was fixed to lowercase source (SPEC 04 compliance).
- **claude-04 note**: `pipeline/language_detector.py` uses `from_all_languages()` (not subset) — detects unsupported langs as OTHER.
- **claude-05 — DONE**: `ai/classifier.py` updated (SPEC 02 prompt, `classify(job_raw: dict)`, corrective retry, list-unwrap fix), `pipeline/quality_gate.py`, `pipeline/orchestrator.py` (ImportPipeline). 202 tests green.
- **claude-05 note**: `⚠ SENIORITY ACCURACY 67.9% < 80% threshold` — needs prompt tuning. See `docs/reports/01-ai-baseline.md`. Role family 89.3% OK. Skills P/R ~74%. See D-05-01.
- **claude-05 note**: Ground truth fixture corrections: de_002, en_004, fr_001 expected_status changed from `valid` → `premium` (SPEC 03 premium criteria clearly met per expected AI outputs).
- **claude-05 note**: Ground truth run: 1 AI failure (Security Engineer, en_008_security_engineer_borderline) — model returned list-wrapped JSON. Fixed post-baseline: `_single_call` now unwraps `[{...}]` to `{...}`.
- **claude-05 note**: `dedupe.merge_with_existing` fixed: naive/aware datetime comparison guard added.
- **claude-06 — DONE**: `connectors/` package with BaseConnector ABC + 12 adapters + registry. 223 tests green.
- **claude-06 note**: scrapers/ unchanged; connectors/ is an adapter layer. fetch() wraps async scrape() via asyncio.run().
- **claude-06 note**: TechMap + JobsCollider remain disabled. Issues tracked in docs/reports/02-connectors-status.md.
- **claude-07 — DONE**: `pipeline/expiration.py` (async HEAD/GET prober, semaphore 10, per-domain rate limit 1 req/s, multilingual body patterns). `import_service/cli.py` with 4 commands: `import`, `expire`, `reindex`, `stats`. `Dockerfile` + `docker-compose.yml` (importer/expirer services). `docs/runbooks/cron.example`. 263 tests green.
- **claude-07 note**: `httpx.Limits` has no `max_connections_per_host`; per-host throttling via `_DomainLimiter` asyncio.Lock.
- **claude-08 — DONE**: `docs/runbooks/migration.md` (7 steps + rollback). `scripts/migrate.py` (--dry-run default, --confirm required for destructive run, idempotent, per-source counts + reject reasons + Groq cost estimate). Dry-run validated: 3,177 raw → 1,466 AI candidates → est cost $0.08 (`docs/reports/03-migration-dryrun.md`).
- **claude-08 note**: dry-run shows LinkedIn / RSS / Jooble / ReteInformaticaLavoro / JobisJob / Jobicy yield 0 imports (mostly DESCRIPTION_TOO_SHORT or MISSING_REQUIRED_FIELDS). Adzuna (735), IProgrammatori (536), Arbeitnow (100), RemoteOK (95) carry the migration. Validate per-source yield post-migration.
- **claude-08 note**: real run BLOCKED on operator preconditions (mongodump, Bun read-only flip, dry-run review). See Project Status above.

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

- **claude-09 — DONE**: README rewrite, docs/runbooks/{troubleshooting,operations,cost-monitoring}.md, .env.example expanded, CHANGELOG.md v1.0.0, MEMORY.md status → STABLE.
- **claude-10 — DONE**: Seniority prompt tuning (`_SYSTEM_PROMPT` rules, D-10-01). Skills lexicon split (`utils/skills_lexicon.py`, D-10-02). Quality gate `_MIN_VALID_SKILLS` 1→2 (D-10-03). CV Drop AI score integrated into quality score (D-10-04). 180 tests green.
- **claude-10 note**: Run `python scripts/run_ai_baseline.py` (needs GROQ_API_KEY) to measure seniority accuracy improvement from 67.9% target 80%+.
- **claude-10 note**: `utils/skills_lexicon.py` has ~300 entries. Add entries as new skills appear in real job postings.

### Watch-list (post-deploy)
- Calibrate quality gate after first run: distribution of `gate_reject_*`.
- Calibrate fuzzy-dup threshold (92) after first month: false-positive rate.
- Validate Groq cost estimate (~$6/month) against actual.
