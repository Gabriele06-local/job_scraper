# Provider Audit — DevBoards Import Service

Full audit table for all registered job connectors.

## Active Providers

| Provider | Type | Auth | Geo | Rate Limit | Status | Notes |
|---|---|---|---|---|---|---|
| Adzuna | REST API | app_id + app_key | gb,us,de,nl,fr,au,ca,at,be,nz | 1s | Enabled | 10-country sweep; 240 calls/day budget |
| Jooble | REST API | api_key | configurable | 0.5s | Enabled | Keyword-based search |
| iProgrammatori | RSS/XML | None | IT | 1s | Enabled | Italian IT jobs RSS feed |
| Arbeitnow | REST JSON | None | DE/EU | 0.5s | Enabled | German market, no auth |
| RemoteOK | REST JSON | None | Worldwide | 0.5s | Enabled | Remote-only jobs |
| Jobicy | REST JSON | None | Worldwide | 0.5s | Enabled | Remote tech jobs |
| RSS | RSS/XML | None | EN | 1s | Enabled | WeWorkRemotely + Jobicy feeds |
| Himalayas | REST JSON | None | Worldwide | 0.5s | Enabled | Remote jobs, credit required |
| Remotive | REST JSON | None | Worldwide | 0.5s | Enabled | 7 IT categories |
| The Muse | REST JSON | THEMUSE_API_KEY | US/global | 0.3s | Enabled | Requires free account |
| Reed | REST JSON | REED_API_KEY (Basic) | GB | 0.5s | Enabled | UK market; HTTP Basic auth |
| Greenhouse | REST JSON | None | Global | 0.5s | Enabled | 100 companies; async parallel fetch |
| Lever | REST JSON | None | Global | 0.5s | Enabled | 100 companies |
| Ashby | REST JSON | None | Global | 0.5s | Enabled | 50 companies; includes compensation |
| Personio | XML | None | EU | 0.5s | Enabled | 50 EU companies; structured XML |

## Removed Providers

| Provider | Type | Removal Reason |
|---|---|---|
| LinkedIn | HTML scraping (BeautifulSoup) | TOS violation; fragile selectors; anti-bot blocks |
| ReteInformaticaLavoro | HTML scraping | Fragile selectors; not maintained |
| JobisJob | HTML scraping | CSS-selector scraping; blocked frequently |
| TechMap | REST API (disabled) | API spec unverified; endpoint unreachable |
| JobsCollider | RSS (disabled) | Category feed returns 404 |

## Data Quality

All providers feed the same pipeline:

```
Fetch → Normalize → Pre-filter → Dedupe → AI Classify (Groq) → Quality Gate → Persist
```

### Pre-filter thresholds (pipeline/prefilter.py)
- Description ≥ 150 chars
- Posted ≤ 60 days ago
- Valid HTTPS URL
- Non-spam title

### Quality gate mandatory fields (pipeline/quality_gate.py)
- `title`, `company_name`, `source_url` — required, non-empty
- `skills` ≥ 2 detected
- `seniority` ≠ UNKNOWN
- `remote_mode` ≠ UNKNOWN
- `employment_type` ≠ UNKNOWN
- `ai_confidence` ≥ 0.6

### AI enrichment (ai/classifier.py)
- Model: `GROQ_MODEL` env (default: llama-3.1-8b-instant)
- Fields enriched: seniority, role_family, remote_mode, employment_type, skills, category, salary (when mentioned), location parsing
- 30 RPM client-side limit

## ATS Company Registry (connectors/ats_registry.json)

| ATS | Companies | Key Companies |
|---|---|---|
| Greenhouse | 100 | Stripe, Figma, Airbnb, Dropbox, Lyft, Coinbase, Notion |
| Lever | 100 | Shopify, Reddit, Scale AI, Duolingo, Wix, Deezer |
| Ashby | 50 | Linear, Vercel, Retool, Loom, Pitch, Fly.io |
| Personio | 50 | Zalando, Delivery Hero, Babbel, Personio, Luko |

## Rate Limits

| Provider | Calls/day | Notes |
|---|---|---|
| Adzuna | 240 (enforced) | Hard budget via pipeline/budget.py + import_budgets collection |
| The Muse | No stated limit | Paginated per category×level |
| Reed | No stated limit | Paginated per keyword |
| Greenhouse | No stated limit | Semaphore(10) parallel; 500ms batch delay |
| Lever | No stated limit | Sequential, per company |
| Ashby | No stated limit | Sequential, per company |
| Personio | No stated limit | Sequential, per company |
| Groq | 30 RPM (free) | Token bucket in ai/classifier.py |
