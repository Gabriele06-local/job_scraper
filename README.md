# DevBoards Import Service

Python service that imports job offers from external portals (RSS, APIs, HTML)
into MongoDB. Consumed by the Bun + Elysia API and Qwik frontend.

## Pipeline

```
Connectors → Normalize → Pre-filter → AI Classify (Groq) → Quality Gate → Persist
                                                         Expiration (separate job)
```

## Prerequisites

- Python 3.11+
- MongoDB 6+ (`itjobhub` database)
- Groq API key
- Adzuna + Jooble keys (optional — connectors auto-disable if missing)
- Node/Bun (test runner only)

## Local Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — minimum required: GROQ_API_KEY, DATABASE_URL, MONGO_DB
```

## Environment Variables

All config via `.env` (Pydantic Settings — see `.env.example` for full list).

### Required

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | AI classification (mandatory) |
| `DATABASE_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `itjobhub` | Database name |

### Connector Auth (optional per connector)

| Variable | Connector |
|---|---|
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Adzuna API |
| `JOOBLE_API_KEY` | Jooble API |

### Tuning

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Groq model |
| `GROQ_RPM` | `30` | Client-side rate limit |
| `DISABLED_CONNECTORS` | `` | Comma-separated connectors to skip |
| `SCRAPE_LANGUAGES` | `it,en,es,fr,de` | Languages to fetch |
| `LOG_LEVEL` | `INFO` | structlog level |
| `EXPIRATION_CONCURRENCY` | `10` | Parallel HEAD probes |
| `EXPIRATION_MAX_AGE_DAYS` | `60` | Force-expire threshold |
| `IMPORT_INTERVAL_SECONDS` | `14400` | Docker importer sleep loop |
| `EXPIRE_INTERVAL_SECONDS` | `86400` | Docker expirer sleep loop |

## CLI Usage

```bash
source venv/bin/activate

# Full import pipeline
python -m import_service.cli import

# Dry run (no DB writes)
python -m import_service.cli import --dry-run

# Limit jobs for testing
python -m import_service.cli import --limit 50

# Expire dead job links
python -m import_service.cli expire

# Rebuild MongoDB indexes (idempotent)
python -m import_service.cli reindex

# Print collection metrics as JSON
python -m import_service.cli stats
```

## Run Tests

```bash
npm test          # pytest via Bun
ruff check .      # lint
```

## Docker Deploy

```bash
# Create external network (once)
docker network create devboards

# Start continuous importer + expirer
docker compose up -d importer expirer

# One-shot operations
docker compose run --rm reindex
docker compose run --rm stats
```

Intervals are controlled by `IMPORT_INTERVAL_SECONDS` and `EXPIRE_INTERVAL_SECONDS`.

## Cron Alternative

See `docs/runbooks/cron.example` for host cron setup.

## First-Run Migration

Before the first production run, wipe legacy data and rebuild indexes.
Follow `docs/runbooks/migration.md` step-by-step.

## Connectors

12 active connectors. 2 disabled (TechMap — API spec unverified, JobsCollider — feed 404).

Disable at runtime: `DISABLED_CONNECTORS=linkedin,jooble`

See `docs/reports/02-connectors-status.md` for per-connector status and known issues.

## Logs

- Structlog JSON to stdout. Redirect to file: `... >> /var/log/devboards/import.log 2>&1`
- Health check JSON: `/tmp/health.json` (path: `HEALTH_CHECK_FILE` env var)

## Docs

| Path | Contents |
|---|---|
| `docs/runbooks/migration.md` | Wipe & re-import procedure |
| `docs/runbooks/troubleshooting.md` | Common errors + fixes |
| `docs/runbooks/operations.md` | Monitoring, log locations |
| `docs/runbooks/cost-monitoring.md` | Groq cost tracking |
| `docs/runbooks/cron.example` | Host cron schedules |
| `docs/specs/` | Architecture + schema SPECs |
| `docs/reports/` | Discovery + baseline + dry-run |
