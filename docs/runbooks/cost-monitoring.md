# Cost Monitoring — Groq AI Usage

## Baseline Estimate

Dry-run (2026-05-01, `docs/reports/03-migration-dryrun.md`):

- Raw offers fetched: 3,177
- Survivors after prefilter: 1,466 (46%)
- Avg cost per Groq call: $0.0000533
- One-time migration cost: **~$0.08**
- Estimated monthly recurring: **~$6/month** (assuming daily 4h import runs)

## Groq Free Tier Limits

Model `llama-3.1-8b-instant`:

| Limit | Value |
|---|---|
| Requests per minute (RPM) | 30 |
| Requests per day (RPD) | 14,400 |
| Tokens per minute (TPM) | 131,072 |
| Tokens per day (TPD) | ~1M |

Client-side rate limit in config: `GROQ_RPM=30` (matches free tier).

## Checking Usage

Groq usage dashboard: https://console.groq.com/usage

Key metrics to track:

- **Requests today** — compare to `persisted` count from `stats` command
- **Tokens today** — ~1,024 output tokens per call (configurable via `GROQ_MAX_TOKENS`)
- **RPM peaks** — spikes indicate the prefilter is under-filtering

## Cost per Import Run

Estimate cost before running:

```bash
python scripts/migrate.py --dry-run 2>&1 | grep groq_cost_usd
```

The migrate script outputs per-source counts and a total cost estimate.
For regular import runs, cost scales linearly with prefilter survivors.

## Reducing Token Spend

Prefilter runs BEFORE AI call — increase strictness to reduce Groq spend:

```
Fetch → Pre-filter (cheap, no AI) → AI Classify (Groq) → ...
```

Prefilter rejects (logged as `prefilter_rejected`):
- Title keyword mismatch (non-IT roles)
- Description < 200 chars (no signal for extraction)
- Already in DB by dedup_hash (skip re-classification by default)

To skip AI on dedupe hits (already classified): dedup_hash match short-circuits
pipeline before Groq call. Do NOT set `--reclassify` unless intentional.

## Alerts

Set up an alert if Groq requests/day exceeds **1,000** on the free tier:

```bash
# Add to daily cron after stats run:
PERSISTED=$(python -m import_service.cli stats | python3 -c "import sys,json; print(json.load(sys.stdin)['total_jobs'])")
```

Or monitor via Groq console — email alerts available in account settings.

## Cost Breakdown by Connector (from dry-run)

| Connector | AI Candidates | Est. Cost |
|---|---|---|
| Adzuna | 735 | $0.039 |
| IProgrammatori | 536 | $0.029 |
| Arbeitnow | 100 | $0.005 |
| RemoteOK | 95 | $0.005 |
| LinkedIn / RSS / Jooble / others | ~0 (prefilter) | ~$0.00 |

Adzuna + IProgrammatori drive ~90% of AI spend. Optimising their
descriptions (or raising the prefilter threshold) has the largest impact.

## Model Upgrade Considerations

`llama-3.1-8b-instant` → `llama-3.3-70b-versatile`:
- ~10× higher seniority accuracy expected (67.9% → ~85%+)
- ~4× higher cost per token
- Stays within free RPM/RPD limits at current volume

Change via `GROQ_MODEL` env var. Re-run baseline (`docs/reports/01-ai-baseline.md`)
after switching.
