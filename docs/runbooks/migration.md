# Migration Runbook — Wipe & Re-import

- Status: APPROVED
- Date: 2026-05-01
- Author: claude-opus-4-7
- Target: production `itjobhub` MongoDB

## Goal

Wipe legacy `jobs` collection (and stale test docs from P1-02), apply new
indexes per SPEC 01 §4, re-import via the new pipeline.

Out-of-scope collections preserved: `news`, `users`, `comments`, `likes`,
`favorites`, `interactions`, `jobs_views`, `contacts`, `companies` (kept,
only `jobs` is dropped).

## Preconditions

- All tests green: `pytest -q`
- Lint clean: `ruff check .`
- Branch `feature/upgrade` merged with claude-08 work
- `.env` populated with `GROQ_API_KEY`, connector keys, `DATABASE_URL`,
  `MONGO_DB`
- Disk has ≥ 2× current jobs collection size for backup
- Bun API maintainer notified, available for read-only flip

## Step 1 — Backup (mongodump)

Take a full dump of `jobs` (and optionally `companies` for safety).

```bash
mkdir -p backups/$(date +%Y%m%d)
mongodump \
  --uri="mongodb://localhost:27017" \
  --db=itjobhub \
  --collection=jobs \
  --out=backups/$(date +%Y%m%d)
```

Verify the dump:

```bash
ls -lh backups/$(date +%Y%m%d)/itjobhub/
# Expect: jobs.bson + jobs.metadata.json, non-zero size
```

Record dump path. Required for rollback.

## Step 2 — Bun API → read-only (manual)

**Operator action.** Switch the Bun + Elysia API to read-only mode so no
writes hit `jobs` mid-migration. Mechanism is API-side (env flag /
deployment), out of scope for this script.

Confirm read-only is in effect before continuing:

- POST/PATCH/DELETE on `/jobs` returns 503 or 405
- GET endpoints still respond

## Step 3 — Drop `jobs` collection

```bash
source venv/bin/activate
python scripts/migrate.py --confirm
```

Script performs:

1. Re-validates `--confirm` flag.
2. Drops `jobs` collection.
3. Recreates indexes via `database.repository.ensure_indexes()`.
4. Runs full re-import via `pipeline.orchestrator.ImportPipeline`.

Without `--confirm` the script runs in dry-run mode (default) and skips
destructive ops.

## Step 4 — Recreate indexes

Indexes are created automatically by step 3. To verify post-run:

```bash
mongosh mongodb://localhost:27017/itjobhub --eval 'db.jobs.getIndexes()'
```

Expected indexes (per `database/repository.py`):

- `_id_`
- `url_unique`
- `dedup_hash_unique`
- `status_posted_at`
- `language_status_posted_at`
- `source`
- `expires_at_sparse`
- `last_probed_at_sparse`
- `location_geo_2dsphere`
- `company_name_normalized`
- `role_family_seniority`
- `text_index`

## Step 5 — Full re-import via pipeline

Triggered by step 3. Logs to stdout (structlog JSON). Watch counters:

- `total`, `prefilter_rejected`, `dedupe_hit`, `gate_valid`,
  `gate_premium`, `gate_rejected`, `persisted`, `groq_cost_usd`

Estimated duration: 5–20 min depending on connector latency and Groq RPM
(default 30/min).

## Step 6 — Smoke test on Bun API

Bun read-only is still ON. Hit a handful of read endpoints and confirm
fresh data:

```bash
curl -s http://localhost:3000/jobs?limit=5 | jq '.[] | {url, title, status}'
curl -s http://localhost:3000/jobs/count | jq .
```

Compare counts vs dump (a delta is expected — old test docs and
prefilter-rejected entries may differ).

## Step 7 — Restore write mode (manual)

**Operator action.** Flip Bun API back to read-write mode.

Confirm POST/PATCH on `/jobs` succeed (or are not regressed).

## Rollback

If smoke test fails or pipeline produced unusable data:

1. Stop any running scrapers / cron jobs.
2. Drop the new `jobs` collection:
   ```bash
   mongosh mongodb://localhost:27017/itjobhub --eval 'db.jobs.drop()'
   ```
3. Restore from dump:
   ```bash
   mongorestore \
     --uri="mongodb://localhost:27017" \
     --db=itjobhub \
     --collection=jobs \
     backups/<YYYYMMDD>/itjobhub/jobs.bson
   ```
4. Verify count matches the pre-migration snapshot.
5. Flip Bun API back to read-write.
6. Open an incident note in `MEMORY.md` Decision Log.

## Post-migration cleanup (T+7 days)

After 7 days of clean operation:

- Drop backup files in `backups/<YYYYMMDD>/`
- Drop legacy `jobs_backup_*` collections if any were created via
  `aggregate $out` instead of mongodump
