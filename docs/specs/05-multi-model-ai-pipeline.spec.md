# SPEC 05 — Multi-Model AI Pipeline (Groq)

- Status: DRAFT
- Date: 2026-05-30
- Author: claude-opus-4-8
- Reviewers: micio86dev

Supersedes the single-model assumption in **SPEC 02 — AI Classification**
(does not change its schema contract; extends the call layer beneath it).
Spans two repos: `job_scraper` (ingestion) and `itjobhub-backend` (search/rank).

---

## 1. Context

Today every job is classified by **one** Groq model — `llama-3.1-8b-instant` —
via a single call in `ai/classifier.py::GroqClassifier._single_call`. The
backend (`src/services/groq/groq.service.ts`) makes one raw `fetch()` to Groq
for CV parsing, and job search is substring `contains` matching with in-memory
Haversine (`src/services/jobs/job.service.ts::getJobs`). There is:

- no model tiering (cheap vs accurate vs reasoning),
- no provider abstraction (the `groq` SDK / raw fetch are called directly),
- no per-task model config, prompt registry, or prompt versioning,
- no AI result cache, no batching, no shared telemetry/cost surface,
- no semantic / relevance ranking in search.

Goals (from product brief): better job quality, less spam, more accurate
parsing, better semantic understanding, **lower AI cost**, scalable ingestion,
better search relevance, better maintainability.

### Hard constraints surfaced during discovery (Decision drivers)

- **C-1 Groq has no embeddings endpoint.** It is LLM-inference-only. Any
  vector/embedding feature needs a *different* provider or a *local* model.
  Per owner decision (2026-05-30): keep the embedding layer **provider-agnostic
  and deferred** — define the interface + schema hooks now, wire no concrete
  embedder. Search relevance is delivered via **lexical retrieve → LLM rerank**.
- **C-2 MongoDB is self-hosted Community (Docker, VPS ports 27018/27019), not
  Atlas.** Atlas Vector Search / Atlas Search are unavailable. Retrieval uses
  the existing Mongo `$text` index (`title^5, description`). No new infra now.
- **C-3 Deterministic logic already covers some "Tier-1" tasks.** Language
  detection (`lingua`) and dedup (`rapidfuzz` hashes) are non-AI today. We do
  **not** move these to an LLM — that would *raise* cost. Tier-1 LLM is reserved
  for tasks that genuinely need a model (triage / spam pre-screen).
- **C-4 Provider-agnostic internally.** Nothing above the provider layer may
  import the `groq` SDK or know a model name. Tasks ask for a *capability*; the
  router picks the model.

---

## 2. Requirements

### Functional

- R-1 Three Groq model tiers, selected per task, never hardcoded at call sites:
  - **Tier FAST** = `llama-3.1-8b-instant` — triage, quick category, light tags,
    cheap cleanup, quick summaries.
  - **Tier STRUCT** = `qwen/qwen3-32b` — structured extraction, noisy HTML/MD
    parsing, salary/stack/seniority/location normalization, remote detection,
    semantic dedup, recruiter-spam detection, company normalization.
  - **Tier REASON** = `llama-3.3-70b-versatile` — *only* for hard residue:
    ambiguous listings, fake-remote / suspicious-recruiter, hidden seniority,
    and (backend) advanced search relevance reranking.
- R-2 A **confidence/difficulty escalation ladder**: start cheap, escalate only
  when the cheaper tier is not confident enough or the input is hard. Most jobs
  must settle at FAST. (See §4.2 — this is the core cost lever.)
- R-3 Per-field confidence scores for: `salary`, `remote_mode`, `seniority`,
  `company_quality`, `technologies` (extends SPEC 02 schema, additive/nullable).
- R-4 Unified, provider-agnostic AI abstraction in **both** repos: provider
  interface, model router, centralized versioned prompts, reusable extraction
  templates, response validation, structured telemetry + token/cost tracking,
  request tracing, retry/fallback/timeout, AI result cache.
- R-5 Backend search: **retrieve (lexical) → optional rerank (REASON)**.
  Embedding-ready schema + interface, deferred wiring (C-1/C-2).
- R-6 Graceful degradation: any tier failure falls back down the ladder or to a
  deterministic result; a provider outage never crashes ingestion or search.

### Non-functional

- N-1 Ingestion throughput must not regress: escalation is the exception, FAST is
  the rule; batching where latency-insensitive.
- N-2 Backward compatible: `GroqClassifier.classify(job_raw)` keeps its
  signature and `JobClassification` return; orchestrator (`pipeline/orchestrator.py:276`)
  is untouched. Backend `extractProfileFromText(text)` keeps its signature.
- N-3 No bare `except`; type hints mandatory (Py); no `Any` in TS. structlog
  (Py) / pino (TS). Config via env (Pydantic Settings / `config/index.ts`).
- N-4 TDD-lite: tests first, full suite green, `ruff` / type-check clean.

---

## 3. Module Map

### 3.1 Scraper (`apps/job_scraper`)

| Module | Path | Responsibility | State |
|--------|------|----------------|-------|
| Provider | `ai/provider.py` | `LLMProvider` protocol + `GroqProvider`: one place that talks to the `groq` SDK. Rate limit, retry/backoff, timeout, per-call token/cost/latency telemetry. | new |
| Router | `ai/router.py` | `ModelRouter`: task→tier config + escalation ladder. Owns "which model, when, and when to escalate". | new |
| Tasks | `ai/tasks.py` | `AITask` enum + per-task config (default tier, schema, prompt id+version, max_tokens, escalation policy). | new |
| Prompts | `ai/prompts/__init__.py` (+ `registry.py`) | Versioned prompt registry: `PROMPTS[task][version]` → system/user templates + schema strings. Reusable extraction templates. | new |
| Cache | `ai/cache.py` | Result cache keyed `sha256(task,prompt_version,model,input)`. Mongo-backed (`ai_cache` coll) + in-proc LRU. Skips re-import calls. | new |
| Telemetry | `ai/telemetry.py` | Per-call record (task, tier, model, tokens, latency, cost, confidence, escalated_from, trace_id) → structlog + `ai_calls` coll + run cost summary. Absorbs current `_CostTracker`. | new (extract) |
| Classifier | `ai/classifier.py` | Refactor: orchestrates EXTRACT (+ escalation) via provider+router; maps to `JobClassification`. No direct `groq` import. | refactor |

### 3.2 Backend (`apps/backend`)

| Module | Path | Responsibility | State |
|--------|------|----------------|-------|
| Provider | `src/services/ai/provider.ts` | `LLMProvider` iface + `GroqProvider` (fetch, retry, timeout, telemetry). | new |
| Router | `src/services/ai/router.ts` | Task→model map + escalation. | new |
| Prompts | `src/services/ai/prompts.ts` | Versioned prompt registry (CV extract, rerank). | new |
| Telemetry/Cache | `src/services/ai/telemetry.ts`, `cache.ts` | Token/cost log + in-proc TTL cache. | new |
| CV service | `src/services/groq/groq.service.ts` | Refactor onto AI layer; keep `extractProfileFromText`. | refactor |
| Search | `src/services/jobs/search.service.ts` | retrieve→rerank; called by `getJobs` when `q` present. | new |
| Job model | `prisma/schema.prisma` | Add nullable `embedding Float[]?`, `embedding_model String?`, `search_text String?` + per-field confidence on import path (additive). | refactor |

---

## 4. Design

### 4.1 The AI abstraction (both repos, same shape)

```
caller (classifier / search / cv) 
   │  asks for a TASK with input + trace_id
   ▼
ModelRouter.run(task, input, ctx)
   │  1. pick entry tier from task config + cheap difficulty signal
   │  2. cache.get(task, version, model, input) → hit? return
   │  3. provider.complete(messages, model, schema, max_tokens, timeout)
   │  4. validate (Pydantic / TS guard) → parse
   │  5. escalate? (see 4.2) → repeat at next tier
   │  6. telemetry.record(...) ; cache.put(...)
   ▼
returns {data, confidence, model, tier, escalated_from, tokens, cost}
```

The **provider** is the *only* module that imports the Groq SDK / hits the API.
The **router** is the *only* module that knows model names. Callers reference
`AITask` capabilities. This satisfies C-4 (provider-agnostic) and R-4.

### 4.2 Routing & escalation ladder (the cost lever — R-2)

Routing is two-dimensional: a **task default tier** + a **dynamic escalation**.

**Entry tier** comes from `ai/tasks.py` config. Default for the main
classification (EXTRACT) is **FAST**, *not* STRUCT — discovery shows 8b already
produces acceptable extractions for the bulk of clean postings, and the brief's
"reduce AI cost / avoid unnecessary expensive calls" requirement dominates the
nominal "structured extraction = 32b" mapping. We honor that mapping for the
cases that *need* it via escalation, not by paying 32b on every job.

**Cheap pre-AI difficulty signal** (no tokens) bumps the entry tier:
- noisy/long HTML (raw length ≫ text length, or > N KB) → enter at STRUCT,
- description < threshold or mostly boilerplate (prefilter flags) → stay FAST,
- source on `low_trust_sources` list → mark for spam check.

**Escalation triggers** (FAST → STRUCT → REASON):
- overall `confidence < 0.7` (mirrors quality_gate reject threshold), or
- a *targeted* low per-field confidence on a field the gate cares about
  (salary/seniority/remote) while signals suggest the data is present, or
- JSON/schema invalid after the in-tier corrective re-prompt, or
- spam/fake-remote suspicion flag set (→ REASON spam-check task).

**Ceilings (no runaway spend):**
- max one escalation step per job by default (`AI_MAX_ESCALATION=1`); REASON only
  if `AI_ENABLE_REASON=true` (default true in prod, false in dev).
- escalation is logged (`ai.escalate`, with `from`/`to`/`reason`) so the rate is
  observable and tunable.

This preserves the brief's intent (32b does the hard structured parsing, 70b the
hard reasoning) while keeping the **modal** job on 8b.

### 4.3 Per-field confidence (R-3)

Extend the EXTRACT schema (additive, all optional) with a `field_confidence`
object: `{salary, remote_mode, seniority, company_quality, technologies}` each
`0..1`. Used to: (a) drive *targeted* escalation, (b) feed quality_gate /
ranking, (c) surface in telemetry. SPEC 02's top-level `confidence` is retained.
`JobClassification` (`models/job.py`) gains an optional `field_confidence` dict;
persisted under `ai.field_confidence` (Mongo), nullable for old docs.

### 4.4 New AI tasks

| Task | Tier (entry) | Output | Notes |
|------|--------------|--------|-------|
| `TRIAGE` | FAST | `{is_it_job, category_guess, spam_likelihood, complexity}` | optional cheap pre-screen; tiny max_tokens; can short-circuit obvious spam before EXTRACT |
| `EXTRACT` | FAST → STRUCT | current classification schema + per-field confidence | the main call; replaces today's single call |
| `NORMALIZE_COMPANY` | STRUCT | `{canonical_name, confidence}` | optional; folds into EXTRACT for now, separable later |
| `SPAM_CHECK` | REASON | `{is_spam, fake_remote, suspicious_recruiter, reason}` | only when suspicion flagged |
| `RERANK` (backend) | REASON | ordered candidate ids + scores | search relevance |

TRIAGE is **opt-in** (`AI_ENABLE_TRIAGE`, default false): it adds a tiny call
per job, only worth it if spam volume is high. EXTRACT remains the always-on
path. This keeps cost flat by default and lets ops turn on triage if spam rises.

### 4.5 Cache (R-4) & batching (N-1)

- **Cache key** = `sha256(task | prompt_version | model | normalized_input)`.
  Backed by Mongo `ai_cache` (TTL index, e.g. 30d) + small in-proc LRU. On
  re-import of an unchanged posting (same URL/content) we skip the call entirely.
  This is the second-largest cost saving after escalation avoidance.
- **Groq prompt cache** preserved: system prompts stay constant (already true in
  `classifier.py`); the registry forbids per-request mutation of system text.
- **Batching**: ingestion is latency-insensitive, so same-task EXTRACT calls may
  be issued concurrently (bounded, mirrors `GROQ_CONCURRENCY` idea in SPEC 00).
  Phase 5 may adopt Groq's batch API where available; v1 uses bounded concurrency
  behind the rate limiter.

### 4.6 Telemetry / cost / tracing (R-4)

One `AICallRecord` per provider call: `trace_id` (run_id+offer_id), `task`,
`tier`, `model`, `tokens_in/out`, `latency_ms`, `cost_usd`, `cache_hit`,
`escalated_from`, `confidence`. Emitted via structlog (`ai.call`) and aggregated
into the existing run cost summary (`pipeline.run_complete`). Optional persist to
`ai_calls` for dashboards (reuses the admin reports surface). Per-model pricing
table lives in `ai/telemetry.py` (FAST/STRUCT/REASON rates), replacing the two
hardcoded constants in `classifier.py`.

### 4.7 Backend search: retrieve → rerank (R-5)

`getJobs` with a `q`:
1. **Retrieve** top-N (e.g. 100) candidates: Mongo `$text` query over the
   existing `title^5, description` text index + structured filters (skills,
   remote, salary, geo) — replaces the O(n) `contains` OR-scan. Typo tolerance
   via fuzzy skill/keyword expansion (reuse `qVariations` idea) and `$text`
   stemming. This alone is a relevance + latency win with zero AI cost.
2. **Rerank (optional, REASON)**: when `rerank=true` (premium / opt-in) send the
   query + the N candidate summaries to `RERANK`; the model returns an ordering
   with relevance scores (intent understanding, stack relevance, skill
   relationships). Cached by `sha256(query | candidate_id_set)`. Falls back to
   lexical order on any failure. Gated by `AI_ENABLE_RERANK`.
3. **Embedding-ready, deferred**: `embedding Float[]?` + `embedding_model` added
   to schema; `EmbeddingProvider` interface defined; **no** concrete embedder and
   **no** vector index (C-1/C-2). A future phase wires a local/external embedder
   and a vector store, switching retrieval to hybrid (lexical ∪ vector) → rerank.

### 4.8 Config surface (additive env)

Scraper `config.py` (Pydantic Settings):
```
groq_model_fast: str = "llama-3.1-8b-instant"
groq_model_struct: str = "qwen/qwen3-32b"
groq_model_reason: str = "llama-3.3-70b-versatile"
ai_confidence_threshold: float = 0.7
ai_max_escalation: int = 1
ai_enable_reason: bool = True
ai_enable_triage: bool = False
ai_cache_enabled: bool = True
ai_cache_ttl_days: int = 30
```
(`groq_model` retained as alias for `groq_model_fast` for back-compat.)
Backend `config/index.ts` mirrors: `ai.models.{fast,struct,reason}`,
`ai.enableRerank`, `ai.maxTokens.{...}`.

---

## 5. Decision Log

- **D-05-1 Default EXTRACT tier = FAST, escalate to STRUCT.** Alternatives: make
  STRUCT the default per the brief's literal mapping; always-STRUCT. Rationale:
  cost goal dominates; 8b is adequate for clean postings (current production
  behavior); escalation gives 32b accuracy exactly where it's needed. ~4–5× cost
  per call avoided on the modal job.
- **D-05-2 Embeddings deferred, provider-agnostic.** Alternatives: local embedder
  now; external embedder now. Rationale: C-1 (Groq has none) + C-2 (no Atlas);
  owner chose lexical+rerank now, interface ready later. Avoids new infra/cost/
  key today.
- **D-05-3 Lexical + LLM rerank, not vector.** Alternatives: Qdrant/pgvector;
  Atlas migration. Rationale: C-2; rerank delivers most relevance gain with zero
  new infra; vector is a clean future add behind the same search seam.
- **D-05-4 Keep deterministic lang-detect & dedup (no Tier-1 LLM).** Rationale:
  C-3; LLM-ifying them raises cost and latency for no quality gain.
- **D-05-5 Provider/router/prompt split.** Alternatives: keep monolithic
  `GroqClassifier`. Rationale: R-4 maintainability + provider-agnosticism;
  enables backend reuse of the same shape; isolates the one SDK touch-point.
- **D-05-6 TRIAGE & REASON opt-in via env.** Rationale: cost stays flat by
  default; ops escalates capability when spam/ambiguity justify it.
- **D-05-7 Cache keyed by content hash, Mongo-backed.** Alternatives: no cache;
  Redis. Rationale: re-imports dominate ingestion; Mongo already present (no new
  infra); TTL index handles eviction.

## 6. Open Questions

- Q-05-1 Persist every `AICallRecord` to `ai_calls`, or only sample? (default:
  sample-on + always aggregate). 
- Q-05-2 Groq batch API availability/limits for `qwen3-32b` — confirm before
  Phase 5 batching beyond bounded concurrency.
- Q-05-3 Rerank candidate count N and token budget vs latency target for search.
- Q-05-4 Backend currently imports jobs with no AI; should backend ever
  re-classify, or remain scraper-owned? (assumption: scraper-owned; backend AI =
  CV + search only).

## 7. Acceptance Criteria

- AC-1 No module above `ai/provider.py` (scraper) / `ai/provider.ts` (backend)
  imports the Groq SDK or references a literal model name.
- AC-2 `GroqClassifier.classify` and `extractProfileFromText` keep signatures;
  existing tests pass unchanged.
- AC-3 A clean clear posting is classified with **one FAST call** (no
  escalation) — asserted by telemetry in a test.
- AC-4 A low-confidence/ambiguous fixture escalates FAST→STRUCT (→REASON if spam)
  — asserted by `escalated_from` in telemetry.
- AC-5 Identical re-import produces a cache hit (zero provider calls) — asserted.
- AC-6 Per-field confidence present on EXTRACT output; persisted nullable.
- AC-7 Backend search with `q` uses `$text` retrieve; `rerank=true` reorders via
  REASON and is cached; both degrade to lexical order on failure.
- AC-8 `ruff` clean (scraper), type-check clean (backend), full suites green.

## 8. Phasing (maps to task list)

1. **This SPEC** (design).
2. Scraper provider abstraction + telemetry extract.
3. Scraper router + escalation ladder; classifier refactor.
4. Prompt registry + per-field confidence + spam/company tasks.
5. AI cache + batching.
6. Backend AI abstraction + router (refactor CV).
7. Backend retrieve→rerank search + embedding-ready schema.
8. Docs: summary, routing/cost rationale, modified files, future work.

Each phase = one git-flow feature branch off `develop`
(`feature/claude-0X-...`), tests-first, `ruff`/type-check green, conventional
commit, MEMORY.md decision-log update.
