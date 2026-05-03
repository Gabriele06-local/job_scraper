# claude-06-connectors-refactor

Type: project
Name: claude-06-connectors-refactor
Description: claude-06 session: connectors refactored under BaseConnector interface

## Summary

claude-06 complete (2026-05-01). connectors/ package created as adapter layer over scrapers/.

**Why:** Pipeline needs unified fetch() → Iterator[dict] interface per SPEC 00. Existing scrapers had inconsistent async scrape(keyword,lang) → List[dict] interface.

**How to apply:** Use connectors.get_enabled_connectors() in any new fetch stage. Do not modify scrapers/ — they are wrapped, not replaced.

## Key Facts

- BaseConnector ABC: source_name, source_type (SourceType enum), rate_limit_seconds, fetch() -> Iterator[dict]
- 10 enabled connectors, 2 disabled (TechMap: API spec unverified; JobsCollider: RSS 404)
- fetch() wraps asyncio.run(scraper.scrape()) — safe in sync context
- Global connectors (Arbeitnow, RemoteOK, Jobicy) call scrape(keyword="", lang="en") once
- Keyword connectors iterate settings.scrape_keywords × settings.scrape_languages
- Registry in connectors/__init__.py: REGISTRY dict + get_enabled_connectors()
- Runtime disable: DISABLED_CONNECTORS env var (comma-separated names)
- 223 tests green, ruff clean
- Issues doc: docs/reports/02-connectors-status.md
