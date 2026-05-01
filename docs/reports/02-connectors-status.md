# 02 — Connectors Status Report

**Date**: 2026-05-01
**Branch**: feature/claude-06-connectors-refactor
**Author**: claude-sonnet-4-6

## Summary

12 connectors inventoried. 10 enabled, 2 disabled.
All enabled connectors wrapped under `BaseConnector` interface (`connectors/`).
Smoke tests green (mocked HTTP). Real-network fetch not run in this session.

## Connector Status Table

| Connector | Source Type | Status | Notes / Issues |
|---|---|---|---|
| LinkedIn | html | enabled | Brittle HTML parser (476 LOC). Frequent breakage risk. P2-01 |
| Adzuna | api | enabled | Requires `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` env vars |
| Jooble | api | enabled | `verify=False` SSL workaround (P1-05). API key required |
| JobisJob | html | enabled | Description placeholder only ("Scraped from JobisJob"). P-quality |
| IProgrammatori | rss | enabled | IT-only. XML custom format. Good data quality |
| Arbeitnow | api | enabled | Unconditional `time.sleep(5)` per HTTP attempt. Slows pipeline |
| RemoteOK | api | enabled | Global remote. Good quality. No auth needed |
| Jobicy | api | enabled | Unconditional `time.sleep(1)`. Low yield (1 job historically) |
| ReteInformaticaLavoro | html | enabled | IT-only. 330 LOC regex-heavy. Detail-page fetch per job |
| RSS | rss | enabled | EN feeds only (WeWorkRemotely, Himalayas, Remotive, Jobicy) |
| **TechMap** | api | **DISABLED** | See issue list below |
| **JobsCollider** | rss | **DISABLED** | See issue list below |

## Disabled Connector Issues

### TechMap

- **Status**: `DISABLED` (marked in `connectors/__init__.py` REGISTRY)
- **Root cause**: API spec not verified. Bearer token (`TECHMAP_API_TOKEN`) not
  present in `.env`. Endpoint `api.techmap.io/v1/jobs` may require paid plan.
- **Action needed**: Obtain valid API token. Verify endpoint schema matches
  existing field mapping. Enable in REGISTRY once verified.

### JobsCollider

- **Status**: `DISABLED` (marked in `connectors/__init__.py` REGISTRY)
- **Root cause**: Category-specific RSS feed
  `jobscollider.com/remote-jobs/software-development.rss` returns 404.
  Main feed `jobscollider.com/remote-jobs.rss` not verified as live.
- **Action needed**: Verify main RSS feed URL is live. Update `_scrape_rss`
  to remove the dead category fallback. Enable in REGISTRY once verified.

## Known Issues (Enabled Connectors)

### P1 / Blocking

| ID | Connector | Issue |
|---|---|---|
| P1-05 | Jooble | `verify=False` SSL in `requests.post`. Security regression. Fix: add CA bundle or skip Jooble if SSL fails. |

### P2 / Quality / Maintainability

| ID | Connector | Issue |
|---|---|---|
| P2-01 | LinkedIn | 476 LOC HTML parser tied to LinkedIn DOM. High maintenance risk. |
| P2-02 | Arbeitnow | Bare `except:` at line 72. Replace with `except Exception`. |
| P2-02 | IProgrammatori | Bare `except:` at line 50. Replace with `except Exception`. |
| P2-05 | Arbeitnow | Unconditional `time.sleep(5)` per attempt. Convert to `tenacity`. |
| P2-05 | Jobicy | Unconditional `time.sleep(1)`. Convert to `tenacity`. |
| P2-06 | JobisJob | Description field is always placeholder text. Pre-filter will reject most. |

## Do Not Fix in This Session

All issues listed above are **tracked, not fixed**. Scope: connector interface
refactor only. Extraction logic must remain unchanged per task spec.
