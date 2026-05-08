---
name: job_scraper-spec
description: Conventions for DevBoards Python import service. Use whenever working on import_service codebase.
---

# DevBoards Import Service Conventions

## SPEC.md Format
- Header: title, status (DRAFT/APPROVED/IMPLEMENTED), date, author, reviewers
- Sections: Context, Requirements, Design, Decision Log, Open Questions, Acceptance Criteria
- Decision Log entry: "Decision: X. Alternatives: Y, Z. Rationale: ...".

## MEMORY.md Update Protocol
After each session:
- Update Last Updated (ISO timestamp)
- Update Project Status (1 line)
- Append Decision Log entries (never delete past ones, mark superseded)
- Refresh Pending Work

## Python Conventions
- Type hints mandatory
- Google-style docstrings
- Explicit error handling, no bare except
- No print(), use structlog
- Config via env (Pydantic Settings)
- Async where existing code is async

## Implementation Workflow
1. Read relevant SPEC.md
2. Write tests first (TDD-lite)
3. Implement minimal code to pass tests
4. Run full test suite — must be green
5. Run lint — must be clean
6. Update MEMORY.md
7. Conventional commit

## Commit Format
- feat(import): new feature
- fix(ai): bug fix
- refactor(pipeline): refactor
- test(connectors): tests
- docs(specs): documentation
- chore(deps): maintenance

## Token Optimization
- Use caveman style throughout
- Reuse existing code paths over new
- Single Groq call per offer with strict JSON schema
- Aggressive pre-filter before AI call
