# Changelog — DevBoards Import Service

All notable changes documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [SemVer](https://semver.org/).

---

## [1.0.0] — 2026-05-01

### Added

**Pipeline (feature/upgrade)**

- `pipeline/` package: `normalize`, `prefilter`, `quality_gate`, `dedupe`,
  `expiration`, `language_detector`, `orchestrator` stages
- `ImportPipeline` orchestrator: Fetch → Normalize → Pre-filter → AI Classify
  → Quality Gate → Persist
- 5-rule quality gate with weighted-sum quality score (0–1)
- 3-layer deduplication: URL, dedup_hash (sha1), fuzzy-title-flag (threshold 92)
- `ExpirationChecker`: async HEAD/GET prober, semaphore 10, per-domain 1 req/s
  rate limit, multilingual body patterns, force-expire on max_age_days

**AI classification (claude-05)**

- `ai/classifier.py`: Groq `llama-3.1-8b-instant`, single JSON-mode call,
  Pydantic validation, 3-attempt retry, list-unwrap fix for malformed responses
- `GroqClassifier.classify(job_raw: dict)` main interface
- Prefilter runs before AI — reduces token spend on unjunk offers

**Connectors refactor (claude-06)**

- `connectors/` package: `BaseConnector` ABC, `SourceType` enum, global registry
- 12 connector adapters wrapping existing `scrapers/` (no scraper code touched)
- Runtime disable via `DISABLED_CONNECTORS` env var
- `get_enabled_connectors()` registry function

**CLI + scheduling (claude-07)**

- `import_service/cli.py`: `import`, `expire`, `reindex`, `stats` commands
- `Dockerfile` + `docker-compose.yml`: `importer` / `expirer` sleep-loop services
- Health check JSON at `/tmp/health.json` after every CLI command
- `docs/runbooks/cron.example`: host cron alternative

**Migration tooling (claude-08)**

- `scripts/migrate.py`: wipe-and-reimport script with `--dry-run` (default) /
  `--confirm` (destructive) gate
- `docs/runbooks/migration.md`: 7-step operator procedure + rollback
- `docs/reports/03-migration-dryrun.md`: dry-run validated (3,177 raw →
  1,466 AI candidates, est. cost $0.08)

**Operational docs (claude-09)**

- `README.md`: complete rewrite — setup, CLI, Docker, env vars, cron
- `docs/runbooks/troubleshooting.md`: common errors + fixes
- `docs/runbooks/operations.md`: monitoring, log locations, health check
- `docs/runbooks/cost-monitoring.md`: Groq cost tracking + alerts
- `.env.example`: all variables documented and commented
- `CHANGELOG.md`: this file

**Infrastructure (claude-03)**

- `config.py`: Pydantic Settings — single source of truth for all env config
- `ruff.toml`: `line-length=100`, per-file ignores for legacy scrapers
- `models/job.py`: `RawJob`, `Job`, `JobClassification` Pydantic models
- `database/repository.py`: `ensure_indexes()` fail-loud at boot (fixes P1-01)

### Changed

- Database name confirmed as `itjobhub` (not `devboards` — docs updated)
- `link` field now written as both `link` AND `url` (double-write, SPEC 01 §6)
- `published_at` now written as both `published_at` AND `posted_at`

### Known Issues

- `GROQ_API_KEY` in `.env` but `ai/categorizer.py` (legacy, unused by new
  pipeline) still references OpenAI — tracked as P1-03; does not affect new
  `import_service.cli import` path
- Seniority accuracy 67.9% on baseline fixtures (threshold 80%) — tracked as
  D-05-01; prompt tuning deferred
- Jooble connector uses `verify=False` for SSL — tracked as P1-05

### Tests

- 263 tests green (`pytest -q`)
- `mongomock` — no real MongoDB required for tests

---

## [0.3.0] — 2026-05-29

### Added

**Schema & Data Quality**
- `connectors/schema.py`: `CanonicalJob` TypedDict with `REQUIRED_KEYS` + `validate()`
- 7 scrapers normalized to canonical keys: adzuna, arbeitnow, iprogrammatori, jobicy, jooble, remoteok, rss
- `_dict_to_raw_job()` simplified in `cli.py`
- Mojibake fix: `utils/text_fixer.py` + migration script
- Geocode backfill: `cmd_geocode` in CLI via Nominatim (OpenStreetMap, free)

**Pipeline Enhancements**
- CV Drop AI: `cv_drop_score` (0..1) in Groq schema, quality score rebalanced
- Quality scoring enhanced (9 dimensions, cv_drop 10%)
- Cross-source dedup: `cross_source_hash`, `find_cross_source_dup()`, `merge_cross_source()`
- Fuzzy dedup upgraded to merge (Stage 2b calls `merge_cross_source()`, skips AI + quality gate)
- Company trust scoring: `pipeline/company_scorer.py` (7 dimensions, Stage 4.5)
- Seniority prompt tuning: title primary signal, senior only for keyword/5+yr, unknown when silent
- Skills lexicon split: `utils/skills_lexicon.py` (~300 skills), `split_skills()` post-Groq

**Connectors**
- 7 RapidAPI connectors enabled: active_jobs_db, faang_watch, hn_hiring, hn_realtime, startup_jobs, workday_jobs, yc_jobs

**Auto-Disable**
- Connectors auto-disabled after 3 consecutive failures
- `disable_provider()` upserts `enabled=False` in `providers` collection
- `should_disable()` / `consecutive_failures()` in `pipeline/import_run.py`

**Tests**
- Coverage: dedupe 21%→100% (21 tests), orchestrator 34%→90% (19 tests)
- Coverage: quality_gate 79%→99% (8 tests), prefilter 73%→100% (11 tests)
- Coverage: report 72%→100% (13 tests), repository 71%→86% (6 tests)
- 5 new connector test files: adzuna, arbeitnow, iprogrammatori, jobicy, remoteok
- Schema validation test in `test_connectors_smoke.py`

### Changed
- `VERSION` bumped to 0.3.0
- All 7 new commits pushed to fork `Gabriele06-local/job_scraper` feature branch

### Known Issues
- PR #5 awaiting review/merge from upstream (`micio86dev:develop`)
- AI baseline cannot run: `GROQ_API_KEY` not set (`.env` missing, only `.env.example`)
- 1 pre-existing test fails: `test_reindex_command` (no MongoDB locally)

---

[0.3.0]: https://github.com/devboards/job-scraper/compare/v1.0.0...v0.3.0
[1.0.0]: https://github.com/devboards/job-scraper/compare/v0...v1.0.0
