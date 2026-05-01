# SPEC 03 — Quality Gate

- Status: DRAFT
- Date: 2026-05-01
- Author: claude-opus-4-7
- Reviewers: micio86dev

---

## 1. Goal

Decide, after AI classification, whether an offer is good enough to expose to the frontend (`status=valid` or `status=premium`) or to keep in the DB but hidden (`status=rejected_quality`). All offers persist — rejected ones support analytics and avoid re-processing on next run.

## 2. Definitions

### 2.1 Valid (must show in default lists)

ALL of the following:

- `len(technical_skills) >= 2` (post-lexicon split — see SPEC 02 §5).
- `seniority != "unknown"`.
- `role_family != "other"`.
- `salary_min is not None` OR `salary_max is not None` OR `remote_mode in ("hybrid", "remote")`.
- `ai_confidence >= 0.7`.

### 2.2 Premium (highlighted / featured tier)

`Valid` AND ALL:

- `len(technical_skills) >= 4`.
- `salary_min is not None` AND `salary_max is not None` (full salary range present).
- `ai_confidence >= 0.85`.
- `quality_flags` includes at least one of `clear_jd`, `has_requirements`.
- `quality_flags` does NOT include `boilerplate`.

### 2.3 Rejected (quality)

Anything else after the AI step. Persisted with `status=rejected_quality`, `reject_reason` set, `quality_score` computed.

### 2.4 Rejected (pre-filter)

Set by SPEC 02 §3 BEFORE AI runs. `status=rejected_prefilter`, `reject_reason` from SPEC 02 §3 table.

## 3. Quality score formula

A single number 0..1 used for ordering and analytics. Computed for ALL non-prefilter-rejected offers.

```
score =
    0.30 * skills_score
  + 0.20 * seniority_score
  + 0.20 * salary_score
  + 0.15 * remote_score
  + 0.15 * confidence_score
```

Component definitions (all clamp 0..1):

| Component | Formula |
|-----------|---------|
| `skills_score` | `min(1.0, len(technical_skills) / 5.0)` |
| `seniority_score` | `1.0` if `seniority != "unknown"` else `0.0` |
| `salary_score` | `1.0` if both `salary_min` and `salary_max` set; `0.6` if only one; `0.0` if neither |
| `remote_score` | `1.0` if `remote_mode == "remote"`; `0.7` if `hybrid`; `0.4` if `onsite`; `0.0` if `unknown` |
| `confidence_score` | `ai_confidence` (already 0..1) |

Stored in `jobs.quality_score`. Premium boost: if `status=premium`, frontend may sort with `quality_score + 0.1` (cap 1.0) to ensure premium-on-top regardless of pure score (frontend concern; not stored).

## 4. Reject reasons (enum)

Stored in `jobs.reject_reason` (string, one of the below). Used for metrics + debugging.

### 4.1 Pre-filter (from SPEC 02 §3)
- `MISSING_TITLE`
- `MISSING_COMPANY`
- `INVALID_URL`
- `MISSING_POSTED_AT`
- `DESCRIPTION_TOO_SHORT`
- `LANGUAGE_NOT_SUPPORTED`

### 4.2 Quality gate
- `INSUFFICIENT_SKILLS` (`len(technical_skills) < 2`)
- `UNKNOWN_SENIORITY`
- `UNKNOWN_ROLE_FAMILY` (`role_family == "other"`)
- `NO_SALARY_AND_NO_REMOTE_MODE` (no salary AND `remote_mode in ("onsite", "unknown")`)
- `LOW_CONFIDENCE` (`ai_confidence < 0.7`)

### 4.3 AI failure
- `AI_UNAVAILABLE` (3 retries failed — SPEC 02 §7)

### 4.4 Lifecycle (set by expiration job, not gate)
- `EXPIRED_404`
- `EXPIRED_410`
- `EXPIRED_REDIRECT`
- `EXPIRED_PATTERN`

**Order of evaluation**: rules in 4.2 are evaluated top-to-bottom, FIRST match wins. So an offer with `skills=[]` AND `seniority=unknown` is reported as `INSUFFICIENT_SKILLS` (not `UNKNOWN_SENIORITY`). Rationale: stable metrics; ops dashboards bucket on first reason.

## 5. Behavior

`pipeline/quality_gate.py` exposes:

```python
def evaluate(classified: ClassifiedJob) -> GatedJob:
    # returns GatedJob with status, reject_reason (or None), quality_score
```

Persisted unconditionally. Listing endpoints in Bun API filter `status in ("valid", "premium")`.

## 6. Metrics

Per-run summary log line emits:
- `gate_pass_valid`
- `gate_pass_premium`
- `gate_reject_INSUFFICIENT_SKILLS`
- `gate_reject_UNKNOWN_SENIORITY`
- `gate_reject_UNKNOWN_ROLE_FAMILY`
- `gate_reject_NO_SALARY_AND_NO_REMOTE_MODE`
- `gate_reject_LOW_CONFIDENCE`
- `gate_reject_AI_UNAVAILABLE`

Plus distribution buckets for `quality_score`: `[0,0.2), [0.2,0.4), ..., [0.8,1.0]`.

## 7. Calibration plan

Discovery shows 39.6% pass strict gate. The new gate is stricter (skills>=2 vs >=1). Expected impact based on current AI quality:
- Pre-filter rejects ~16-20% (short descriptions, missing posted_at).
- Quality gate rejects 30-40% of post-AI offers.
- Net `valid+premium` yield: ~45-55% of fetched offers.
- Premium yield: estimated 5-15% of valid (very salary-dependent).

Calibration target: after first full run, review `gate_reject_*` distribution. If `LOW_CONFIDENCE` dominates, prompt may need tightening (SPEC 02). If `NO_SALARY_AND_NO_REMOTE_MODE` dominates, consider weakening rule for offers from sources with reliable `original_language`+seniority (e.g. Adzuna).

## 8. Decision Log (this SPEC)

- **Decision**: 5-rule valid gate with first-match reject_reason. **Alternatives**: composite score threshold; multi-reason list. **Rationale**: simple to monitor; clear root cause for each rejection; aligns with SPEC requirement (`skills>=2, seniority!=unknown, role_family!=other, (salary OR remote_mode), confidence>=0.7`).
- **Decision**: weighted-sum quality score (0.30/0.20/0.20/0.15/0.15). **Alternatives**: equal weights; percentile-based. **Rationale**: skills count drives perceived quality most strongly per discovery; salary is rare (81.7% missing) so weight 0.20 not 0.30 to avoid systematic deflation.
- **Decision**: persist rejected offers (don't drop). **Alternatives**: discard rejected. **Rationale**: re-processing cost is the AI call; persisting `dedup_hash` prevents re-running AI on the same junk on next run. Storage cost negligible.
- **Decision**: Premium tier requires both `clear_jd` or `has_requirements` flag AND no `boilerplate`. **Alternatives**: skip flag check. **Rationale**: AI confidence alone misclassifies polished-but-empty boilerplate as high-confidence. Flag check is the cheap correction.
- **Decision**: `remote_mode in (hybrid, remote)` qualifies a missing-salary offer as `valid`. **Alternatives**: salary required. **Rationale**: 81.7% missing salary today — requiring salary collapses yield to <20%. Remote-mode is a strong proxy signal that an offer is fully specified.

## 9. Out of Scope

- ML-trained classifier on top of AI confidence (future).
- Per-source quality multipliers (e.g. trust LinkedIn more than RSS).
- User-feedback signals (likes / clicks) feeding back into score — Bun API concern.
