# Setup Guide — DevBoards Import Service

## Prerequisites

- Python 3.11+
- MongoDB (local or Atlas)
- `npm` (for test runner)

## Local Setup

```bash
cd apps/job_scraper
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set GROQ_API_KEY and DATABASE_URL
```

## Required Environment Variables

| Variable | Required | Where to get |
|---|---|---|
| `GROQ_API_KEY` | Yes | https://console.groq.com/keys (free) |
| `DATABASE_URL` | Yes | MongoDB URI — local or Atlas |
| `MONGO_DB` | No | Default: `itjobhub` |

## Optional Provider Keys

| Variable | Provider | Registration |
|---|---|---|
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Adzuna | https://developer.adzuna.com/ |
| `JOOBLE_API_KEY` | Jooble | https://jooble.org/api/about |
| `REED_API_KEY` | Reed | https://www.reed.co.uk/developers/jobseeker |
| `THEMUSE_API_KEY` | The Muse | https://www.themuse.com/developers |

No key required for: Himalayas, Remotive, RemoteOK, Arbeitnow, Greenhouse, Lever, Ashby, Personio.

## Running the Importer

```bash
# Full import (all enabled connectors)
python -m import_service.cli import

# Dry run (no DB writes)
python -m import_service.cli import --dry-run

# Single connector
python -m import_service.cli import --connector greenhouse

# Expire stale listings
python -m import_service.cli expire
```

## Running Tests

```bash
# Unit tests (no network, no DB)
npm test

# Live provider audit (real HTTP calls)
LIVE=1 pytest tests/test_providers_live_audit.py -v -s

# Single provider live test
LIVE=1 pytest tests/test_providers_live_audit.py::test_provider_live[greenhouse] -v -s
```

## Lint

```bash
./venv/bin/ruff check .         # check
./venv/bin/ruff check . --fix   # auto-fix
```

## GitHub Secrets (CI/CD)

Add these in repo Settings → Secrets and variables → Actions:

| Secret | Required |
|---|---|
| `GROQ_API_KEY` | Yes |
| `DATABASE_URL` | Yes |
| `ADZUNA_APP_ID` | Optional |
| `ADZUNA_APP_KEY` | Optional |
| `JOOBLE_API_KEY` | Optional |
| `REED_API_KEY` | Optional |
| `THEMUSE_API_KEY` | Optional |

## Docker / Production

Inject env vars directly (do NOT commit `.env` to repo):

```bash
docker run \
  -e GROQ_API_KEY=gsk_... \
  -e DATABASE_URL=mongodb+srv://... \
  -e ADZUNA_APP_ID=... \
  -e ADZUNA_APP_KEY=... \
  job_scraper:latest
```

Or use `docker-compose.yml`:

```yaml
environment:
  GROQ_API_KEY: ${GROQ_API_KEY}
  DATABASE_URL: ${DATABASE_URL}
```

## Adding a New Connector

1. Create `scrapers/{name}_scraper.py` — HTTP client, returns `list[dict]`
2. Create `connectors/{name}.py` — `BaseConnector` subclass with `fetch() -> Iterator[dict]`
3. Register in `connectors/__init__.py` REGISTRY dict
4. Add config keys to `config.py` if auth required
5. Add env vars to `.env.example`
6. Write tests: `tests/test_connector_{name}.py`
7. Run `npm test` + `ruff check .`

## MongoDB Collections

| Collection | Purpose |
|---|---|
| `jobs` | Normalized job offers |
| `import_runs` | Per-provider run records (ImportRunRecord) |
| `import_budgets` | Daily API call counters (Adzuna budget) |
