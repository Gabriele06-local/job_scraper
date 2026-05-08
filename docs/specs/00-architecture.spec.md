# SPEC 00 — Target Architecture

- Status: DRAFT
- Date: 2026-05-01
- Author: claude-opus-4-7
- Reviewers: micio86dev

---

## 1. Goal

Replace the current synchronous, AI-on-everything orchestrator with a staged pipeline that filters cheaply before paying for Groq, validates before persisting, and tracks lifecycle (first_seen → expired). Output: a single `jobs` collection with stable schema for the Bun API.

## 2. Pipeline (target)

```mermaid
flowchart LR
  A[Fetch<br/>scrapers/*] --> B[Normalize<br/>pipeline/normalize.py]
  B --> C[Pre-filter<br/>pipeline/prefilter.py<br/>NO AI]
  C -->|reject| Z1[(status=<br/>rejected_prefilter)]
  C -->|hit dedup| H[update last_seen]
  C -->|pass| D[AI Classify<br/>ai/classifier.py<br/>Groq, 1 call]
  D --> E[Quality Gate<br/>pipeline/quality_gate.py]
  E -->|fail| Z2[(status=<br/>rejected_quality)]
  E -->|pass| F[Persist<br/>database/repository.py]
  F --> G[(jobs<br/>status=valid|premium)]
  X[Expiration Job<br/>pipeline/expiration.py<br/>HTTP HEAD, daily] --> G
```

Stages run per-offer. Pre-filter is the cost gate — kills ≥30% of inputs (short desc, missing fields, dedupe hits) before any Groq call.

## 3. Module Map

| Module | Path | Responsibility | Existing? |
|--------|------|----------------|-----------|
| Orchestrator | `main.py` | CLI + run pipeline + metrics emit | refactor |
| Config | `config.py` (new, root) | Pydantic Settings, env loading | new |
| Scrapers | `scrapers/*.py` | Fetch raw offers (12 connectors) | existing |
| Normalize | `pipeline/normalize.py` | Map raw → canonical `RawJob` model | new |
| Pre-filter | `pipeline/prefilter.py` | Lang detect, length, required fields, dedup probe | new |
| AI classify | `ai/classifier.py` | Groq single-call, JSON-schema validated | refactor `ai/categorizer.py` |
| Quality gate | `pipeline/quality_gate.py` | Apply rules → status/score/reject_reason | new |
| Persist | `database/repository.py` | Upsert jobs/companies, indexes | refactor `database/mongo_client.py` |
| Dedupe | `pipeline/dedupe.py` | Hash + fuzzy detect | refactor `utils/deduplicator.py` |
| Expiration | `pipeline/expiration.py` | HEAD probe, mark expired | new |
| Logging | `utils/logging.py` | structlog JSON setup | new |
| Metrics | `utils/metrics.py` | Per-stage counters → log + optional Prometheus | new |

### Dir change justification (Decision Log entry)

**New `pipeline/` package**: existing layout (ai/, scrapers/, utils/, database/) has no home for stage logic. Putting normalize/prefilter/gate inside `utils/` would bloat that bucket; inside `ai/` would mislead. `pipeline/` is the orchestration boundary — every file in it represents one pipeline stage.

## 4. Tech Stack

| Concern | Choice | Replaces / Notes |
|---------|--------|------------------|
| HTTP (sync) | `httpx` | replaces `requests`; consistent with async path |
| HTTP (async) | `httpx.AsyncClient` | replaces `aiohttp` |
| RSS | `feedparser` | unchanged |
| HTML parse | `BeautifulSoup4 + lxml` | unchanged |
| Config | `pydantic-settings` | replaces `os.getenv` ad-hoc |
| Validation | `pydantic v2` | for RawJob, ClassifiedJob, PersistedJob models |
| AI | `groq` SDK | replaces `openai`; model from `GROQ_MODEL` env |
| Lang detect | `lingua-py` (or `fasttext-langdetect`) | local, no AI |
| Fuzzy match | `rapidfuzz` | for dedup fuzzy layer |
| Logging | `structlog` | JSON output, correlation IDs |
| Retry | `tenacity` | exponential backoff for HTTP + Groq |
| DB | `pymongo` (sync) | unchanged; async not needed at current volume |
| Geocoding | `googlemaps` (existing) | now cached via `cachetools` LRU |
| Tests | `pytest + pytest-asyncio + responses` | extend existing |
| Lint | `ruff` | replaces flake8 |

## 5. Data Flow Models

```
RawJob (post-Normalize)
  url, title, description, company_name, source, posted_at?,
  location_raw?, salary_min?, salary_max?, currency?, original_language?

PrefilterResult
  decision: pass | reject | duplicate
  reject_reason?: enum
  detected_language: str

ClassifiedJob (post-AI, validated)
  + skills[], category, seniority, role_family, employment_type,
    remote_mode, salary_min?, salary_max?, currency?,
    languages_required[], quality_flags[], confidence

GatedJob (post-Quality Gate)
  + status (valid | premium | rejected_quality), reject_reason?,
    quality_score (0..1)

PersistedJob = jobs document (see SPEC 01)
```

## 6. Concurrency & Scheduling

- Per-run: scrapers iterate sequentially (already I/O-bound; concurrency at scraper level optional). AI calls batched with `asyncio.gather` capped at `GROQ_CONCURRENCY=4`.
- Schedule: cron entry shipped in `docs/runbooks/cron.example`. Granularity: hourly fetch, daily expiration probe.
- No Docker / systemd in scope (open question Q-05); leave runbook with cron snippet only.

## 7. Observability

- **Logging**: structlog JSON. Correlation ID per run (`run_id = uuid4`), per-offer (`offer_id = sha1(url)`). Levels: INFO (stage transitions), WARNING (retries, soft failures), ERROR (hard failures).
- **Metrics** (per stage, per run, emitted at end as one summary log line):
  - `fetched_total`, `normalized_ok`, `prefilter_pass`, `prefilter_reject_<reason>`, `dedup_hit`, `ai_call_total`, `ai_call_failed`, `gate_pass_valid`, `gate_pass_premium`, `gate_reject_<reason>`, `persisted_new`, `persisted_updated`, `groq_tokens_in`, `groq_tokens_out`.
- **Cost tracker**: tokens × Groq price → emitted per-run.

## 8. Error Handling

- HTTP: `tenacity` with `wait_exponential(min=1,max=30)` + `stop_after_attempt(3)` on 429/5xx/timeout.
- Groq: same retry; on persistent fail → log ERROR, mark offer `status=rejected_quality`, `reject_reason=AI_UNAVAILABLE`. Do NOT crash run.
- Mongo: bulk_write with `ordered=False`. DuplicateKey on `dedup_hash` → expected, treat as dedup hit. Other errors → log + skip.
- No bare `except`. All `except Exception as e:` log with `exc_info=True`.

## 9. Decision Log (this SPEC)

- **Decision**: Introduce `pipeline/` package. **Alternatives**: keep stages inside `utils/` or `main.py`. **Rationale**: clear stage boundary; makes pipeline order discoverable; matches the architecture diagram 1:1.
- **Decision**: Replace `requests` + `aiohttp` with `httpx` (sync + async). **Alternatives**: keep both. **Rationale**: one library, identical API surface, modern, supports retries via `tenacity` cleanly.
- **Decision**: `pydantic-settings` for config. **Alternatives**: continue `os.getenv` + manual `dotenv`. **Rationale**: typed, validated, single source of truth; reflects SPEC requirement "Config via env (Pydantic Settings)".
- **Decision**: structlog JSON logging. **Alternatives**: keep stdlib logging. **Rationale**: SPEC mandates structlog; correlation IDs across stages require contextvars binding which structlog handles natively.
- **Decision**: `tenacity` for retry. **Alternatives**: hand-rolled per scraper. **Rationale**: removes per-scraper inconsistency (P2-05); decorator-based, testable.

## 10. Out of Scope (this SPEC)

- Containerization (Q-05).
- Switching to Bun API as write path (Q-06) — direct Mongo write retained; revisit after stability proven.
- News scraper sibling service.
