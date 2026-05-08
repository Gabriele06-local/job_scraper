# Troubleshooting — DevBoards Import Service

## Connection Errors

### `ServerSelectionTimeoutError` / `connection refused`

MongoDB not running.

```bash
# Local
mongod --dbpath /data/db

# Check URI
echo $DATABASE_URL   # must be mongodb://localhost:27017
```

### `OperationFailure: auth failed`

Wrong credentials in `DATABASE_URL`. Check `MONGO_DB` matches the actual DB name
(`itjobhub`, not `devboards`).

---

## Groq / AI Errors

### `GROQ_API_KEY not set` or `AuthenticationError`

```bash
echo $GROQ_API_KEY   # must start with gsk_
```

Set in `.env` and reload shell / restart service.

### `RateLimitError` from Groq

Reduce `GROQ_RPM` (default `30`). Free tier limit is 30 RPM / 14,400 RPD on
`llama-3.1-8b-instant`. The classifier retries 3× with backoff — transient
spikes resolve automatically.

### `ai_unavailable` counter high in logs

Groq is timing out or returning invalid JSON. Check:

1. `GROQ_API_KEY` valid and account has quota
2. `GROQ_TIMEOUT` (default `30`s) — raise if network is slow
3. Description content — very short descriptions trigger `DESCRIPTION_TOO_SHORT`
   prefilter before AI, so they shouldn't reach Groq

---

## Import Yields 0 Jobs

### All connectors return 0

- Check that `.env` credentials are set: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
  `JOOBLE_API_KEY`
- Run single connector manually:

  ```bash
  python -c "
  from connectors import get_enabled_connectors
  for c in get_enabled_connectors():
      jobs = list(c.fetch())
      print(c.source_name, len(jobs))
      break
  "
  ```

### Only Adzuna/IProgrammatori/RemoteOK yield results

LinkedIn, Jooble, RSS, ReteInformaticaLavoro, JobisJob, Jobicy are known to
yield 0 after the prefilter (description too short or missing required fields).
See `docs/reports/03-migration-dryrun.md` for per-source yield data.

### `dedupe_hit` count equals `total`

All jobs already in DB. Add new keywords or run after `expire` to re-surface
expired listings.

---

## Index Errors

### `DuplicateKeyError` on `url_unique`

Duplicate URL in source feed. The dedup layer should catch this before persist.
If it slips through:

```bash
python -m import_service.cli reindex   # recreates indexes, no data loss
```

### `link` unique index missing (legacy P1-01)

Old DB did not create the index. Run:

```bash
python -m import_service.cli reindex
```

Verify:
```bash
mongosh mongodb://localhost:27017/itjobhub --eval 'printjson(db.jobs.getIndexes())'
```

---

## Expiration Issues

### `expire` command marks nothing

No active jobs to probe, or all were probed recently. Check:

```bash
python -m import_service.cli stats
# look at "never_probed" and "active" counts
```

### `TransportError` / `SSLError` during expiration probe

Expected for dead links. The expirer marks these as `transient` (not expired)
on first failure. After 3 consecutive failures, the job is expired. This is
by design.

---

## Container Issues

### `network devboards not found`

```bash
docker network create devboards
docker compose up -d importer expirer
```

### Container restarts in a loop

Check logs:
```bash
docker compose logs importer --tail 50
```

Common cause: `GROQ_API_KEY` missing in `.env` — the import run fails, the
sleep still fires, the loop repeats.

---

## Test Failures

### `mongomock` errors

Tests use `mongomock` — no real MongoDB needed. Ensure `mongomock` is installed:

```bash
pip install -r requirements.txt
npm test
```

### `pytest: collected 0 items`

Missing test discovery. Run from repo root with venv active:

```bash
source venv/bin/activate
pytest -q
```

### `ruff check .` failures

Auto-fix safe rules:
```bash
ruff check . --fix
```

Legacy files in `scrapers/` have per-file ignores in `ruff.toml` — do not
remove them.
