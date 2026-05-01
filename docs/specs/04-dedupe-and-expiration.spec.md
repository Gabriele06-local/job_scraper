# SPEC 04 — Dedupe & Expiration

- Status: DRAFT
- Date: 2026-05-01
- Author: claude-opus-4-7
- Reviewers: micio86dev

---

## 1. Goal

Two lifecycle concerns:

1. **Dedupe** — prevent the same offer from being persisted twice (and from spending AI tokens twice). Fix P1-01 (silent index failure) and address the 88 cross-source duplicate groups.
2. **Expiration** — mark offers `status=expired` once their listing is gone, so the frontend stops showing dead links.

## 2. Dedupe

### 2.1 Layers

Three layers, evaluated in order. Earliest hit short-circuits.

| Layer | Where | Cost | Decision |
|-------|-------|------|----------|
| L1 — URL exact | DB unique idx `url` | DuplicateKey on insert | Update `last_seen_at`, exit. |
| L2 — Hash exact | DB unique idx `dedup_hash` | DuplicateKey on insert | Same as L1. |
| L3 — Fuzzy | App-level rapidfuzz | O(N) per offer over 14-day window — cheap (≤1k candidates) | Flag, do NOT merge (write `quality_flags+=["fuzzy_dup_candidate"]`, set `fuzzy_dup_of=<other_id>`); persist as new doc with own `dedup_hash`. |

### 2.2 Hash specification

```python
def dedup_hash(title: str, company_name: str, source: str) -> str:
    norm = lambda s: unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode().lower()
    norm = lambda s: re.sub(r"\s+", " ", norm(s)).strip()
    payload = f"{norm(title)}|{norm(company_name)}|{source.lower()}"
    return hashlib.sha1(payload.encode()).hexdigest()
```

Notes:
- Includes `source` to avoid false-merge across boards (e.g. "Software Engineer" at "Google" appears on every board).
- Excludes `posted_at` — same offer reposted on the same board on different days IS a dedupe target (we want to update `last_seen_at`, not insert).
- Excludes location — same offer / same board / different city is rare and likely a different listing.

### 2.3 Fuzzy detection

Per offer, before insert:

```python
candidates = db.jobs.find({
    "company.name_normalized": rj.company.name_normalized,
    "posted_at": {"$gte": now - 14d},
    "source": {"$ne": rj.source},
})
for c in candidates:
    score = fuzz.token_sort_ratio(rj.title_normalized, c.title_normalized)
    if score >= 92:
        rj.quality_flags.append("fuzzy_dup_candidate")
        rj.fuzzy_dup_of = c._id
        break
```

Threshold 92: empirically (rapidfuzz docs + manual sample) ≥92 catches "Sr Backend Engineer" vs "Senior Backend Engineer" at the same company while rejecting "Backend Engineer" vs "Backend Lead".

Fuzzy hits are NOT merged. They are flagged so:
- analytics can quantify cross-source overlap;
- frontend can collapse-by-company at render time if desired (not required by this SPEC);
- future SPEC may convert fuzzy hits into hard merges once threshold is calibrated.

### 2.4 Merge logic (when L1 / L2 hit)

On `DuplicateKeyError` during insert:
```python
db.jobs.update_one(
    {"$or": [{"url": rj.url}, {"dedup_hash": rj.dedup_hash}]},
    {"$set": {"last_seen_at": now, "updated_at": now},
     "$inc": {"seen_count": 1}}
)
```

Fields NEVER overwritten on dedupe hit:
- `first_seen_at`, `created_at`, `ai_*`, `quality_score`, `status`, `reject_reason`,
- AI-derived fields (skills, seniority, salary, …) — unless explicit `--reclassify` CLI flag.

Fields ALWAYS refreshed:
- `last_seen_at`, `updated_at`, `seen_count` (+1).

Optional refresh (gated by config flag `REFRESH_DESCRIPTION_ON_DEDUP`, default `false`):
- `description`, `description_md` — useful if source corrects a typo, but most-of-the-time wasted writes.

### 2.5 Index hygiene at boot

```python
def ensure_indexes():
    db.jobs.create_index("url", unique=True)
    db.jobs.create_index("dedup_hash", unique=True)
    # ... others (SPEC 01 §4)
```

NO try/except. NO swallow. If the index can't be built, the process exits non-zero. Fixes P1-01.

If a wipe-and-reimport is in progress, indexes are created BEFORE first insert (so DuplicateKey can be the dedupe signal from offer #2 onward).

## 3. Expiration

### 3.1 Why a separate job

Expiration must run independently from fetch:
- A re-fetch run already takes minutes; piggybacking HEAD probes inflates that.
- Expiration cadence (daily) differs from fetch cadence (hourly).
- Failure of expiration must not affect fetch reliability.

### 3.2 Job design

`pipeline/expiration.py`. CLI: `python main.py expire [--limit 500] [--max-age-days 60]`.

Picker query:
```python
db.jobs.find({
    "status": {"$in": ["valid", "premium"]},
    "$or": [
        {"last_probed_at": None},
        {"last_probed_at": {"$lt": now - 24h}},
    ],
}).sort([("last_probed_at", 1)]).limit(LIMIT)
```

Default `LIMIT=500/run`, `CONCURRENCY=10` (httpx async).

### 3.3 Probe

```python
async def probe(url: str) -> ExpirationVerdict:
    r = await client.head(url, timeout=10, follow_redirects=False)
    if r.status_code in (404, 410):
        return Verdict(expired=True, reason=f"EXPIRED_{r.status_code}")
    if r.status_code in (301, 302, 303, 307, 308):
        if redirect_target_matches_expired_pattern(r.headers["location"]):
            return Verdict(expired=True, reason="EXPIRED_REDIRECT")
        return Verdict(expired=False)  # benign redirect
    if r.status_code == 451:
        return Verdict(expired=True, reason="EXPIRED_410")  # legal removal
    if 500 <= r.status_code < 600:
        return Verdict(expired=None, transient=True)  # retry next day
    if r.status_code == 200:
        # Some boards return 200 with "this job is no longer available" body
        # GET only when source is in EXPIRY_BODY_PATTERNS allow-list to limit cost
        if source in EXPIRY_BODY_PATTERNS:
            body = await client.get(url, timeout=10).text
            if any(p.search(body) for p in EXPIRY_BODY_PATTERNS[source]):
                return Verdict(expired=True, reason="EXPIRED_PATTERN")
        return Verdict(expired=False)
    return Verdict(expired=False)
```

`EXPIRY_BODY_PATTERNS`: dict per `source` with regex list. Bootstrapped empty; populated as we see 200-with-dead-body cases (live in `pipeline/expiration_patterns.py`).

### 3.4 Updates

```python
db.jobs.update_one(
    {"_id": job._id},
    {"$set": {
        "last_probed_at": now,
        **({"status": "expired",
            "reject_reason": verdict.reason,
            "expires_at": now} if verdict.expired else {}),
        "updated_at": now,
    }}
)
```

Transient (5xx) → only `last_probed_at` updated; the picker will re-pull the job next run because `last_probed_at < now - 24h` will become false for a day, then true again.

Stop-condition: jobs older than `MAX_AGE_DAYS=60` AND never probed expired → mark `status=expired`, `reject_reason=EXPIRED_PATTERN`. Prevents stale `valid` accumulating when source goes dark.

### 3.5 Robots.txt & politeness

- Per-host concurrency cap: `httpx` `Limits(max_connections_per_host=2)`.
- Random jitter `0..200ms` between requests to same host.
- Skip `robots.txt` check for HEAD on job pages (industry-standard interpretation: robots.txt governs crawlers, not health probes; reconsider if any board complains).
- `User-Agent: DevBoardsLinkProbe/1.0 (+https://devboards.io/probe)`.

## 4. Cadence

| Task | Cadence | Trigger |
|------|---------|---------|
| Fetch + classify + persist | Hourly | cron |
| Expiration probe | Daily, 03:00 UTC | cron |
| Backup `jobs` snapshot | Weekly | manual / cron (runbook) |

Cron snippets in `docs/runbooks/cron.example` (post-implementation).

## 5. Decision Log (this SPEC)

- **Decision**: 3 dedupe layers (URL, hash, fuzzy-flag). **Alternatives**: URL only; fuzzy-merge instead of fuzzy-flag. **Rationale**: URL alone misses re-listings to a different URL; fuzzy-merge risks false-positive collapses ("Software Engineer at Google" cross-board); flag-only preserves data, gives time to calibrate.
- **Decision**: `dedup_hash` includes `source`. **Alternatives**: cross-source hash. **Rationale**: cross-source hash collides genuine distinct listings (same generic title, same big-company, different boards). Use fuzzy layer for cross-source detection instead.
- **Decision**: fuzzy threshold 92 / 14-day window. **Alternatives**: 85 / 30 days. **Rationale**: tighter threshold prevents false-positives during initial deployment; window matches typical job-listing repost cycle. Tune after first month of data.
- **Decision**: expiration is HEAD-only by default, GET-on-allow-list. **Alternatives**: GET always. **Rationale**: HEAD is ~10× cheaper bandwidth, faster, and politer. GET reserved for boards we know return 200-with-dead-body.
- **Decision**: index creation at boot, fail-loud. **Alternatives**: keep current swallow. **Rationale**: P1-01 root cause. A pipeline depending on uniqueness must guarantee uniqueness exists.
- **Decision**: dedupe hit does NOT re-run AI by default. **Alternatives**: re-run on every hit. **Rationale**: AI classification is the dominant cost; re-running on dedupe hit is wasteful when the offer body has not changed materially. CLI `--reclassify` flag for ops override.

## 6. Out of Scope

- Semantic-similarity dedup (embeddings).
- Cross-language dedup (same offer translated to two languages).
- Per-source archive of expired offers (they remain in `jobs` with `status=expired`; analytics workflows query directly).
