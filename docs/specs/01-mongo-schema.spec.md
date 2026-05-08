# SPEC 01 — MongoDB Schema & Bun API Contract

- Status: DRAFT
- Date: 2026-05-01
- Author: claude-opus-4-7
- Reviewers: micio86dev

---

## 1. Goal

Define the canonical `jobs` schema for the `itjobhub` database, the indexes that enforce dedupe and serve query patterns, the wipe-and-reimport migration plan, and the stable contract exposed to the Bun + Elysia API.

## 2. Database

- **URI**: `mongodb://localhost:27017`
- **DB name**: `itjobhub` (canonical). NOT `devboards`. Decision Log entry below.
- **Collections in scope**: `jobs`, `companies`, `seniorities` (legacy, kept for backward compat).

## 3. `jobs` collection — schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `_id` | ObjectId | Y | |
| `url` | String | Y | Canonical offer URL. **Unique idx**. (Renamed from `link` — see §6.) |
| `source` | String | Y | Scraper id, e.g. `linkedin`, `adzuna`. |
| `external_id` | String\|null | N | Source-native id when available. |
| `dedup_hash` | String | Y | `sha1(title_normalized\|company.name_normalized\|source)`. **Unique idx**. |
| `title` | String | Y | Original offer title. |
| `title_normalized` | String | Y | Lowercased, accents stripped, whitespace collapsed. |
| `description` | String | Y | Full text. ≥200 chars enforced by pre-filter. |
| `description_md` | String\|null | N | Markdown variant if available. |
| `language` | Enum | Y | `en\|it\|es\|de\|fr\|pt\|other`. Detected by `lingua-py`. |
| `posted_at` | Date | Y | Renamed from `published_at`. Pre-filter rejects if missing. |
| `first_seen_at` | Date | Y | Set on insert. |
| `last_seen_at` | Date | Y | Updated on dedupe hit. |
| `expires_at` | Date\|null | N | Set by expiration job. |
| `last_probed_at` | Date\|null | N | Set by expiration job. |
| `status` | Enum | Y | `valid\|premium\|rejected_quality\|rejected_prefilter\|expired`. |
| `reject_reason` | String\|null | N | Enum from SPEC 03 §4 when status=rejected_*. |
| `company.name` | String | Y | |
| `company.name_normalized` | String | Y | Same rules as `title_normalized`. |
| `company.logo` | String\|null | N | |
| `company.id` | ObjectId\|null | N | FK to `companies._id`. |
| `location.raw` | String\|null | N | As scraped. |
| `location.formatted_address` | String\|null | N | From geocoder. |
| `location.city` | String\|null | N | |
| `location.country` | String\|null | N | ISO-3166 alpha-2. |
| `location.geo` | GeoJSON Point\|null | N | `{type:"Point", coordinates:[lng,lat]}`. **2dsphere idx**. |
| `remote` | Boolean | Y | True iff `remote_mode in (hybrid, remote)`. |
| `remote_mode` | Enum | Y | `onsite\|hybrid\|remote\|unknown`. |
| `employment_type` | Enum | Y | `full_time\|part_time\|contract\|freelance\|internship\|unknown`. |
| `technical_skills` | [String] | Y | Lowercased, deduped, max 30. |
| `skills` | [String] | Y | Soft skills + general categories (kept separate from technical). |
| `category` | String\|null | N | E.g. `web_dev`, `data_eng`. |
| `role_family` | Enum | Y | `frontend\|backend\|fullstack\|devops\|data\|ml\|mobile\|qa\|security\|design\|pm\|other`. |
| `seniority` | Enum | Y | `junior\|mid\|senior\|lead\|principal\|unknown`. |
| `seniority_id` | ObjectId\|null | N | Legacy FK; kept for current Bun API compat (§6). |
| `salary_min` | Int\|null | N | Yearly, currency-denominated. |
| `salary_max` | Int\|null | N | |
| `currency` | String\|null | N | ISO-4217, e.g. `EUR`, `USD`. |
| `languages_required` | [String] | Y | Spoken languages (e.g. `en`, `it`). May be empty. |
| `requirements` | [String] | N | Bullets extracted by AI. |
| `benefits` | [String] | N | |
| `quality_flags` | [String] | N | E.g. `clear_jd`, `has_responsibilities`. |
| `quality_score` | Number | Y | 0..1. From SPEC 03 §3. |
| `ai_confidence` | Number | Y | 0..1. From AI. |
| `ai_model` | String | Y | E.g. `llama-3.1-8b-instant`. |
| `ai_call_at` | Date | Y | When AI was invoked. |
| `views_count` | Int | Y | Default 0. Bun-managed. |
| `clicks_count` | Int | Y | Default 0. Bun-managed. |
| `likes_count` | Int | Y | Default 0. Bun-managed. |
| `dislikes_count` | Int | Y | Default 0. Bun-managed. |
| `comments_count` | Int | Y | Default 0. Bun-managed. |
| `created_at` | Date | Y | |
| `updated_at` | Date | Y | |

## 4. Indexes

| Name | Spec | Rationale |
|------|------|-----------|
| `url_unique` | `{url: 1}` unique | Hard dedupe at DB level (fixes P1-01). |
| `dedup_hash_unique` | `{dedup_hash: 1}` unique | Cross-source dedupe (title+company+source). DuplicateKey is the dedup signal. |
| `status_posted_at` | `{status: 1, posted_at: -1}` | Frontend list query (latest valid+premium). |
| `language_status_posted_at` | `{language: 1, status: 1, posted_at: -1}` | Per-language listing. |
| `source` | `{source: 1}` | Per-source ops queries, ablation. |
| `expires_at_sparse` | `{expires_at: 1}` sparse | Expiration batch query. |
| `last_probed_at_sparse` | `{last_probed_at: 1}` sparse | Expiration job picker. |
| `location_geo_2dsphere` | `{"location.geo": "2dsphere"}` | Map / radius queries. |
| `company_name_normalized` | `{"company.name_normalized": 1}` | Company filter; precondition for fuzzy aggregation. |
| `role_family_seniority` | `{role_family: 1, seniority: 1}` | Faceted listing. |
| `text_index` | `{title: "text", description: "text"}` weights `{title:5, description:1}` | Free-text search (frontend). |

`companies`:
- `name_unique` `{name: 1}` unique
- `name_normalized_unique` `{name_normalized: 1}` unique

`seniorities` (legacy, deprecate post-migration):
- `level_unique` `{level: 1}` unique (already exists)

## 5. Migration — wipe & re-import

Approved per CLAUDE.md ("Wipe & re-import strategy approved"). Steps:

1. **Backup** `itjobhub.jobs` → `itjobhub.jobs_backup_20260501` via `mongodump` or `aggregate $out`.
2. Likewise `companies` → `companies_backup_20260501`.
3. **Drop** `jobs`, `companies` content (preserve `news`, `users`, `comments`, `likes`, `favorites`, `interactions`, `jobs_views`, `contacts`, etc — out of scope).
4. Apply new indexes on empty collections (idempotent — fail-fast on error, NOT silent like P1-01).
5. Run pipeline end-to-end. First run is full re-import.
6. After 7 days of clean operation, drop backup collections.

Rollback: if pipeline produces unusable data, restore via `mongorestore` from backup.

**Test data purge**: P1-02's 333 test docs (example.com / test.com URLs) are eliminated by step 3.

## 6. Bun API Contract (stable surface)

The Bun + Elysia API consumes Mongo directly via Prisma. To avoid breaking it during this upgrade, the scraper writes a schema that is a **superset** of what Bun reads, with these mapping rules:

| Bun / Frontend reads | Mongo field | Mapping note |
|----------------------|-------------|--------------|
| `link` / `externalLink` | `url` | **Bun aliases `url` as `link`** OR scraper writes both `url` and `link` for transition. **Decision: write both during transition window (1 release).** |
| `published_at` / `publishDate` | `posted_at` | Bun aliases. Scraper also writes `published_at` mirror for 1 release. |
| `availability` (junior\|mid\|...) | `employment_type` + `remote_mode` | Mapping done in API layer. Scraper does NOT pre-compute. |
| `seniority` (junior\|mid\|senior\|unknown) | `seniority` | New values `lead`, `principal` added — frontend must accept or downgrade. **Action: frontend update required (out of scope this repo, raise PR in Qwik repo).** |
| `companyLogo` | `company.logo` | unchanged |
| `companyScore`, `companyLikes`, `companyDislikes` | NOT scraper-written | Bun-managed. |

**Compatibility window**: `link` AND `url`, `published_at` AND `posted_at` written for one release cycle. After Bun migration to read new names, scraper drops legacy fields. Tracked in Pending Work.

## 7. Decision Log (this SPEC)

- **Decision**: DB name = `itjobhub` (not `devboards`). **Alternatives**: rename DB to `devboards` to match SPEC docs; run a copy. **Rationale**: `itjobhub` already has 14 collections shared with Bun API + News scraper. Renaming requires coordinated mods in Bun, frontend, news_scraper, MCP config — high risk, low value. Update CLAUDE.md and `.mcp.json` instead.
- **Decision**: rename `link` → `url`, `published_at` → `posted_at`. **Alternatives**: keep current names. **Rationale**: `link` is HTML-jargon; `url` is the standard. `published_at` is ambiguous (job published when? site published when?), `posted_at` matches typical job-board semantics. Worth the 1-release compat window.
- **Decision**: `dedup_hash = sha1(title_normalized|company.name_normalized|source)`. **Alternatives**: include `posted_at`; cross-source hash (drop `source`). **Rationale**: cross-source same job (e.g. LinkedIn + Adzuna mirror) is currently 88 groups (discovery §4). Merging across sources is desirable but risks false-positives for generic titles ("Software Engineer" at "Google"). Keep `source` in hash for safety; flag fuzzy cross-source dupes (SPEC 04) without merging.
- **Decision**: keep `seniorities` collection for legacy compat; deprecate post-migration. **Alternatives**: drop now. **Rationale**: Bun API may still resolve `seniority_id`; defer cleanup until Bun PR confirms.
- **Decision**: `unique` indexes are MANDATORY at boot. **Alternatives**: lazy creation. **Rationale**: P1-01 was caused by silent `IndexError`. Boot fails loudly if index can't be built (e.g. duplicate-key conflict during migration).
- **Decision**: write both `link`+`url` and `published_at`+`posted_at` during 1-release transition. **Alternatives**: hard cutover. **Rationale**: scraper and Bun deploy on different cadences; double-write avoids a coordinated cutover risk.

## 8. Out of Scope

- Async pymongo (Motor): volume does not warrant.
- Time-series / sharding: 2086 docs today; not needed.
- Vector search on description (semantic dedup): future SPEC.
