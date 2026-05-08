# Migration Dry-Run Report

- **Date**: 2026-05-01T11:03:11Z
- **Mode**: dry-run (no DB writes)
- **Database**: mongodb://localhost:27017/itjobhub?replicaSet=rs0&w=1&journal=true / itjobhub
- **Elapsed**: 534.2s

## Connectors

- Enabled: 10 → linkedin, adzuna, jooble, jobisjob, iprogrammatori, arbeitnow, remoteok, jobicy, reteinformaticalavoro, rss
- Disabled: 2 → techmap, jobscollider

## Totals

| Stage | Count |
|-------|------:|
| Raw fetched | 3,177 |
| Normalized | 3,144 |
| Pre-filter rejected | 1,711 |
| Pre-filter passed (AI candidates) | 1,466 |

## Per Source

| Source | Would Import | Rejected |
|--------|------------:|---------:|
| Adzuna | 735 | 38 |
| Arbeitnow | 100 | 0 |
| IProgrammatori | 536 | 18 |
| Jobicy | 0 | 50 |
| JobisJob | 0 | 0 |
| Jooble | 0 | 320 |
| LinkedIn | 0 | 744 |
| RSS | 0 | 157 |
| RemoteOK | 95 | 2 |
| ReteInformaticaLavoro | 0 | 381 |

## Reject Reasons

| Reason | Count |
|--------|------:|
| DESCRIPTION_TOO_SHORT | 1,127 |
| MISSING_REQUIRED_FIELDS | 527 |
| NORMALIZE_FAILED | 32 |
| SUSPECTED_SPAM | 24 |

## Estimated Groq Cost

- AI candidates: 1,466
- Avg cost per call (baseline): $0.000053
- **Estimated total**: $0.0782

Notes: cost is an upper bound — dedupe hits skip the AI call.
Salary, seniority, and quality-gate stages run only on candidates.

## Next Steps

1. Review counts per source. Investigate any unexpected zeros.
2. Review top reject reasons. Tune pre-filter if a source is
   over-rejected.
3. Confirm estimated cost is acceptable.
4. Follow `docs/runbooks/migration.md` to execute with `--confirm`.
