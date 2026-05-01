# Operations — DevBoards Import Service

## Service Topology

| Service | Command | Schedule |
|---|---|---|
| `importer` | `python -m import_service.cli import` | Every 4h (Docker sleep loop) |
| `expirer` | `python -m import_service.cli expire` | Every 24h (Docker sleep loop) |
| `reindex` | `python -m import_service.cli reindex` | Weekly (cron/CI, one-shot) |
| `stats` | `python -m import_service.cli stats` | Daily (cron/CI, one-shot) |

Intervals via env: `IMPORT_INTERVAL_SECONDS` (default 14400), `EXPIRE_INTERVAL_SECONDS`
(default 86400).

## Log Locations

### Docker

```bash
docker compose logs importer --tail 100 -f
docker compose logs expirer  --tail 100 -f
```

### Cron / bare metal

Redirect stdout at cron entry — see `docs/runbooks/cron.example`:

- `/var/log/devboards/import.log`
- `/var/log/devboards/expire.log`

### Log format

structlog JSON, one event per line. Key fields:

```json
{"event": "cli.import.done", "total": 312, "persisted": 180, "gate_valid": 95,
 "gate_premium": 42, "gate_rejected": 75, "prefilter_rejected": 38,
 "dedupe_hit": 19, "ai_unavailable": 0}
```

## Health Check File

Path: `/tmp/health.json` (override: `HEALTH_CHECK_FILE` env var).

```json
{
  "last_command": "import",
  "last_run_at": "2026-05-01T08:00:00+00:00",
  "counters": {"persisted": 180, "total": 312}
}
```

Use for container health checks or external monitors:

```bash
# last run within 5h?
python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta
h = json.load(open('/tmp/health.json'))
age = datetime.now(timezone.utc) - datetime.fromisoformat(h['last_run_at'])
sys.exit(0 if age < timedelta(hours=5) else 1)
"
```

## Metrics (stats command)

```bash
python -m import_service.cli stats
```

Output (JSON):

```json
{
  "as_of": "...",
  "total_jobs": 1453,
  "active": 1100,
  "expired": 250,
  "never_probed": 320,
  "by_status": {"valid": 900, "premium": 200, "rejected": 353}
}
```

Key ratios to watch:

| Metric | Healthy range | Action if out of range |
|---|---|---|
| `persisted / total` | > 30% | Check prefilter + gate thresholds |
| `ai_unavailable` | < 5% | Check Groq quota / key |
| `expired / active` | < 30% | Run `expire` if stale links piling up |
| `never_probed` count | Falling over time | Increase `EXPIRATION_CONCURRENCY` |

## MongoDB Indexes

Verify indexes at any time:

```bash
mongosh mongodb://localhost:27017/itjobhub --eval 'printjson(db.jobs.getIndexes())'
```

Expected (per `database/repository.py`):

- `_id_`, `url_unique`, `dedup_hash_unique`, `status_posted_at`,
  `language_status_posted_at`, `source`, `expires_at_sparse`,
  `last_probed_at_sparse`, `location_geo_2dsphere`, `company_name_normalized`,
  `role_family_seniority`, `text_index`

Rebuild (idempotent):

```bash
python -m import_service.cli reindex
```

## Connector Registry

Inspect enabled connectors at runtime:

```bash
python3 -c "
from connectors import get_enabled_connectors
for c in get_enabled_connectors():
    print(c.source_name, c.__class__.__name__)
"
```

Disable a connector without code change:

```bash
DISABLED_CONNECTORS=linkedin,jooble python -m import_service.cli import
```

## Database Backup

Run before any destructive operation:

```bash
mkdir -p backups/$(date +%Y%m%d)
mongodump \
  --uri="mongodb://localhost:27017" \
  --db=itjobhub \
  --collection=jobs \
  --out=backups/$(date +%Y%m%d)
```

See `docs/runbooks/migration.md` for full migration + rollback procedure.

## Routine Maintenance

| Cadence | Task |
|---|---|
| Weekly | Run `reindex` (idempotent, safe) |
| Monthly | Review `stats` output; adjust `EXPIRATION_MAX_AGE_DAYS` if needed |
| After 7d post-migration | Drop `backups/<YYYYMMDD>/` |
| Per incident | Append entry to `MEMORY.md` Decision Log |
