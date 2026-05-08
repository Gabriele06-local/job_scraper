# AI Baseline Report — Ground Truth (30 offers)

- **Date**: 2026-05-01T10:01:18Z
- **Model**: llama-3.1-8b-instant
- **Offers classified**: 28 / 30 (1 skipped — prefilter reject, no ai_output)
- **AI failures**: 1
- **Elapsed**: 61s

## Summary Metrics

| Metric | Value | Flag |
|--------|-------|------|
| Seniority accuracy | 67.9% | ⚠ <80% |
| Role family accuracy | 89.3% | OK |
| Skills precision (avg) | 74.2% | |
| Skills recall (avg) | 73.7% | |
| Skills Jaccard (avg) | 62.1% | |
| Salary MAE | n/a | |

## Cost

- Groq tokens in: 21,018
- Groq tokens out: 6,716
- Total cost (30 calls): $0.0016
- Estimated cost/1k offers: $0.06
- Estimated cost/10k offers: $0.57

## Per-Offer Results

| # | Title | Seniority OK | Role OK | Skills P | Skills R | Salary MAE |
|---|-------|:---:|:---:|:---:|:---:|---:|
| 1 | Senior Java Backend Entwickler (Spr | ✓ | ✓ | 67% | 67% | 0 |
| 2 | Frontend Entwickler React TypeScrip | ✗ (mid→senior) | ✓ | 100% | 75% | 0 |
| 3 | IT Allrounder | ✓ | ✓ | 67% | 67% | — |
| 4 | Senior Backend Engineer (Go) | ✓ | ✓ | 86% | 60% | 0 |
| 5 | Fullstack Engineer TypeScript | ✓ | ✓ | 89% | 100% | 0 |
| 6 | Senior DevOps / SRE Engineer | ✓ | ✓ | 100% | 80% | 0 |
| 7 | Frontend Developer Angular | ✓ | ✓ | 71% | 71% | 0 |
| 8 | Data Engineer (dbt + BigQuery) | ✓ | ✓ | 86% | 75% | 0 |
| 9 | iOS Developer Swift | ✓ | ✓ | 62% | 71% | 0 |
| 10 | Junior Frontend Developer React | ✓ | ✓ | 100% | 100% | — |
| 11 | Backend Developer PHP | ✓ | ✓ | 100% | 86% | 0 |
| 12 | Administrative Assistant | ✓ | ✓ | 20% | 100% | — |
| 13 | Senior Backend Developer Node.js | ✓ | ✓ | 100% | 88% | 0 |
| 14 | Desarrollador Fullstack React + PHP | ✗ (mid→senior) | ✓ | 67% | 57% | 0 |
| 15 | Ingeniero DevOps Docker + CI/CD | ✗ (mid→senior) | ✓ | 88% | 88% | — |
| 16 | Desarrollador Web Junior | ✗ (junior→unknown) | ✗ (frontend→other) | 0% | 0% | — |
| 17 | Técnico de Soporte IT | ✓ | ✓ | 67% | 50% | — |
| 18 | Développeur Backend Python | ✗ (mid→senior) | ✓ | 100% | 88% | 0 |
| 19 | Développeur Web Junior | ✓ | ✓ | 60% | 60% | — |
| 20 | Senior Python Backend Engineer | ✓ | ✓ | 100% | 78% | 0 |
| 21 | Fullstack Developer React + Django | ✗ (mid→senior) | ✓ | 50% | 71% | — |
| 22 | DevOps Engineer Kubernetes | ✓ | ✓ | 88% | 88% | — |
| 23 | Senior Data Engineer | ✓ | ✓ | 100% | 100% | 0 |
| 24 | Frontend Developer Vue.js | ✓ | ✓ | 75% | 86% | 0 |
| 25 | Mobile Developer Android Kotlin | ✓ | ✓ | 50% | 71% | — |
| 26 | UI/UX Designer | ✗ (mid→senior) | ✓ | 88% | 88% | 0 |
| 27 | QA Engineer Junior | ✗ (junior→unknown) | ✗ (qa→other) | 0% | 0% | — |
| 28 | Sviluppatore Software | ✗ (unknown→mid) | ✗ (fullstack→backend) | 100% | 100% | — |

**AI failures**: 1 offer(s) returned None after 3 retries.

## Action Required

- Seniority accuracy 67.9% < 80% threshold. Flag in MEMORY.md for prompt tuning.
