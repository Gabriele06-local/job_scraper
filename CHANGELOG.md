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

[1.0.0]: https://github.com/devboards/job-scraper/compare/v0...v1.0.0
