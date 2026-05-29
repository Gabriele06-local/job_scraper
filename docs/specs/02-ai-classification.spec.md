# SPEC 02 — AI Classification (Groq, single call)

- Status: DRAFT
- Date: 2026-05-01
- Author: claude-opus-4-7
- Reviewers: micio86dev

---

## 1. Goal

One Groq call per offer that survives the pre-filter, returning a strict-JSON object validated against a Pydantic schema. Inputs minimized; outputs cover everything the Quality Gate (SPEC 03) and frontend need.

## 2. Provider & Model

- Provider: Groq.
- Model: from env `GROQ_MODEL`, default `llama-3.1-8b-instant`.
- API key: env `GROQ_API_KEY`.
- Concurrency: env `GROQ_CONCURRENCY`, default `4`.
- Timeout: 30s per call.

## 3. Pre-filter Rules (gate BEFORE the call)

Pre-filter runs in `pipeline/prefilter.py`. **All rules must pass** (else `status=rejected_prefilter`, `reject_reason=<rule>`):

| Rule | Reject reason | Detail |
|------|---------------|--------|
| Title non-empty | `MISSING_TITLE` | `len(title.strip()) > 0` |
| Company non-empty | `MISSING_COMPANY` | `company.name not in ("", "Unknown", None)` |
| URL valid http(s) | `INVALID_URL` | starts with `http://` or `https://`, parses |
| Posted-at present | `MISSING_POSTED_AT` | non-null Date |
| Description length | `DESCRIPTION_TOO_SHORT` | `len(description) >= 200` |
| Detected language supported | `LANGUAGE_NOT_SUPPORTED` | `lingua-py` ∈ `{en, it, es, de, fr, pt}`. Else accept as `other` ONLY if scraper marked `original_language` ∈ supported (signal trust). |
| Dedupe miss | (not a reject — short-circuit) | `dedup_hash` not in DB. On hit: update `last_seen_at`, exit pipeline. |

Pre-filter eliminates an estimated ≥30% of inputs before any Groq cost (based on discovery: 16% have desc<200; ~5% missing posted_at; dedup hit rate climbs after first run).

## 4. Prompt

System prompt (constant, cache-friendly):

```
You are a strict job-listing classifier. You receive a job offer and return ONLY
a JSON object that conforms to the provided schema. No prose, no markdown, no
explanations. If a field is unknown, use the schema's "unknown" enum value or
null per the schema. Do not invent skills or salary numbers. Confidence is your
self-assessment of overall extraction reliability (0..1).

Seniority rules — use the TITLE as the primary signal, then the description:
- "senior" only when the title explicitly contains "Senior"/"Sr." or the
  description requires 5+ years of experience
- "junior" when the title contains "Junior"/"Jr."/"Entry"/"Trainee" or the
  posting says "no experience required"
- "mid" for roles with 1-4 years of experience and NO seniority keyword in
  the title
- "unknown" when no experience level or seniority keyword is mentioned at all;
  do NOT invent a seniority level — "unknown" is correct when the posting
  is silent
```

User prompt (per offer):

```
TITLE: {title}
COMPANY: {company_name}
LOCATION: {location_raw or "unknown"}
DETECTED_LANGUAGE: {detected_language}
DESCRIPTION (truncated to 4000 chars):
{description_truncated}

Return JSON conforming to schema:
{json_schema_str}
```

Description truncation: 4000 chars (Groq llama-3.1-8b-instant context allows more, but truncation caps cost variance and is rarely lossy for classification — first 4000 chars carry role / requirements; benefits / legal text live past that).

## 5. JSON Schema (output)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "skills", "category", "seniority", "role_family",
    "employment_type", "remote_mode",
    "salary_min", "salary_max", "currency",
    "languages_required", "quality_flags", "confidence"
  ],
  "properties": {
    "skills": {
      "type": "array",
      "items": {"type": "string"},
      "maxItems": 30
    },
    "category": {"type": "string"},
    "seniority": {
      "type": "string",
      "enum": ["junior", "mid", "senior", "lead", "principal", "unknown"]
    },
    "role_family": {
      "type": "string",
      "enum": ["frontend", "backend", "fullstack", "devops", "data",
               "ml", "mobile", "qa", "security", "design", "pm", "other"]
    },
    "employment_type": {
      "type": "string",
      "enum": ["full_time", "part_time", "contract", "freelance",
               "internship", "unknown"]
    },
    "remote_mode": {
      "type": "string",
      "enum": ["onsite", "hybrid", "remote", "unknown"]
    },
    "salary_min": {"type": ["integer", "null"], "minimum": 0},
    "salary_max": {"type": ["integer", "null"], "minimum": 0},
    "currency": {
      "type": ["string", "null"],
      "pattern": "^[A-Z]{3}$"
    },
    "languages_required": {
      "type": "array",
      "items": {"type": "string", "minLength": 2, "maxLength": 3}
    },
    "quality_flags": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["clear_jd", "has_responsibilities", "has_requirements",
                 "has_benefits", "has_tech_stack", "vague", "boilerplate"]
      }
    },
    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
  }
}
```

Validation: pydantic v2 model mirrors this schema; classifier raises `AIValidationError` on mismatch → handled by retry logic (§7).

`technical_skills` post-processing: `skills` from AI is split locally — items matching a curated technical-skills lexicon (`utils/skills_lexicon.py`, kept slim, ~500 entries: languages, frameworks, cloud, DBs) go to `technical_skills`; rest stays in `skills`. Avoids polluting AI output with two near-identical fields.

## 6. I/O Examples

### Example 1 — clean senior remote offer
**Input** (truncated):
```
TITLE: Senior Backend Engineer (Go)
COMPANY: Acme Cloud
LOCATION: Remote, EU
DETECTED_LANGUAGE: en
DESCRIPTION: We are hiring a senior backend engineer to scale our Go-based
microservices on AWS (EKS, RDS). 5+ years of production Go required.
Postgres, Kafka, gRPC. Salary: 80k-110k EUR. Full remote within EU. ...
```

**Output**:
```json
{
  "skills": ["Go", "AWS", "EKS", "RDS", "Postgres", "Kafka", "gRPC", "microservices"],
  "category": "backend_engineering",
  "seniority": "senior",
  "role_family": "backend",
  "employment_type": "full_time",
  "remote_mode": "remote",
  "salary_min": 80000,
  "salary_max": 110000,
  "currency": "EUR",
  "languages_required": ["en"],
  "quality_flags": ["clear_jd", "has_requirements", "has_tech_stack"],
  "confidence": 0.93
}
```

### Example 2 — vague mid-level on-site
**Input**:
```
TITLE: Sviluppatore Web
COMPANY: WebStudio Srl
LOCATION: Milano, Italia
DETECTED_LANGUAGE: it
DESCRIPTION: Cerchiamo sviluppatore web con esperienza per progetti su misura.
Conoscenza HTML, CSS, JavaScript. Sede di lavoro: Milano. Inviare CV. ...
```

**Output**:
```json
{
  "skills": ["HTML", "CSS", "JavaScript"],
  "category": "web_development",
  "seniority": "mid",
  "role_family": "frontend",
  "employment_type": "full_time",
  "remote_mode": "onsite",
  "salary_min": null,
  "salary_max": null,
  "currency": null,
  "languages_required": ["it"],
  "quality_flags": ["vague"],
  "confidence": 0.62
}
```
(Confidence < 0.7 → SPEC 03 rejects as `LOW_CONFIDENCE` AFTER gate.)

### Example 3 — boilerplate / non-tech polluting input
**Input**:
```
TITLE: Customer Support Representative
COMPANY: Generic Co
LOCATION: Berlin
DETECTED_LANGUAGE: en
DESCRIPTION: Join our team! We are looking for motivated individuals to join
our growing customer support team. Native English speakers, weekend shifts...
```

**Output**:
```json
{
  "skills": [],
  "category": "customer_support",
  "seniority": "junior",
  "role_family": "other",
  "employment_type": "full_time",
  "remote_mode": "onsite",
  "salary_min": null,
  "salary_max": null,
  "currency": null,
  "languages_required": ["en"],
  "quality_flags": ["boilerplate"],
  "confidence": 0.71
}
```
(SPEC 03 rejects: `role_family=other` AND `skills < 2`.)

## 7. Retry & Backoff

`tenacity`-based:
- Retry on: HTTP 429, 5xx, timeout, `AIValidationError` (JSON shape mismatch), Groq SDK transient errors.
- Wait: `wait_exponential(multiplier=1, min=1, max=30)` + `wait_random(0,1)` jitter.
- Stop: `stop_after_attempt(3)`.
- After 3 failures: log ERROR, return `None`. Caller marks job `status=rejected_quality`, `reject_reason=AI_UNAVAILABLE`. Pipeline continues.

Note: Groq supports JSON-mode via `response_format={"type": "json_object"}`. We use it AND validate with Pydantic — defense in depth (model occasionally returns extra keys / wrong enum literal).

## 8. Cost Estimate

Per-call tokens (typical):
- Input: ~1500 tokens (system 200 + schema 700 + prompt 600).
- Output: ~300 tokens.

Groq `llama-3.1-8b-instant` published price (as of 2026-05): ~$0.05 / 1M input tokens, ~$0.08 / 1M output tokens.

Per-call cost ≈ (1500 × 0.05 + 300 × 0.08) / 1e6 ≈ **$0.000099/call** ≈ **$0.0001**.

Daily volume estimate: 2000 unique offers/day → ~$0.20/day → **~$6/month**.
Monthly worst-case (5000 offers/day, 30 days): **~$15/month**.

System-prompt caching: Groq does not currently expose explicit cache pricing comparable to Anthropic; system prompt remains constant which is automatically friendly to provider-side caching where available. Re-validate price page before launch (Decision Log: cost is a moving target).

## 9. Error Cases

| Error | Handling |
|-------|----------|
| Groq 429 | Retry per §7. After 3: skip job, NEXT run picks it up (no `last_ai_attempt` field needed). |
| Groq 401/403 | Fail loud — config error; halt run. |
| Pydantic validation fail | Retry per §7 (provider sometimes returns malformed). After 3: skip with `AI_UNAVAILABLE`. |
| Description >4000 chars | Truncate; do not retry on success. |
| Empty `skills` array | Allowed by schema; SPEC 03 rejects on count<2. |

## 10. Decision Log (this SPEC)

- **Decision**: Single Groq provider, no fallback. **Alternatives**: dual Groq + OpenAI failover. **Rationale**: simplicity > 99.9% availability for a non-realtime pipeline. Retry handles transient outages; persistent outage delays import by hours, acceptable. Revisit if Groq SLA proves insufficient.
- **Decision**: 4000-char description truncation. **Alternatives**: token-based truncation; full body. **Rationale**: char-based is deterministic without a tokenizer dependency; first 4000 chars carry classification signal in 95%+ of jobs (manual sample, n=20).
- **Decision**: Pydantic validation on top of Groq JSON-mode. **Alternatives**: trust Groq JSON-mode. **Rationale**: defense in depth — JSON-mode does not enforce enum literals or required keys, only valid JSON.
- **Decision**: Skills lexicon split done locally, NOT by AI. **Alternatives**: ask AI for `technical_skills` and `soft_skills` separately. **Rationale**: deterministic; smaller schema; lexicon updates do not require prompt re-tuning. Lexicon committed to repo.
- **Decision**: 3-attempt retry. **Alternatives**: 5+ attempts. **Rationale**: Groq transient error rates are low; longer retry chains delay run completion without measurable yield. Job re-enters pipeline next run anyway.

## 11. Out of Scope

- Few-shot examples in prompt (would inflate input tokens 3-4×). Re-evaluate if confidence distribution shows recurrent miss-classification.
- Embedding-based dedup (semantic): future SPEC.
- Multi-model voting: cost-prohibitive at current price tier.
