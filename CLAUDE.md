# CLAUDE.md — DevBoards Import Service

## Project Context
Python service that imports job offers from external portals (RSS, APIs, HTML) into MongoDB.
Consumed by Bun + Elysia API and Qwik frontend (out of scope for this repo).

## Active Work Branch
All current work happens on `feature/upgrade`.
New features branch FROM `feature/upgrade` (not develop), and merge back to `feature/upgrade`.

## Git-Flow Conventions

### Branch Strategy
- `master`: production
- `develop`: integration (untouched during this upgrade work)
- `feature/upgrade`: current upgrade work parent
- `feature/claude-XX-name`: per-task branches off `feature/upgrade`

### Per-Feature Workflow (mandatory for every task)
1. Start: `git flow feature start claude-XX-name` (auto from develop, but we override)
2. Implementation per SPEC + skill conventions
3. Run tests: `npm test` — must pass
4. Run lint: `ruff check .` — must pass
5. Commit (conventional commits): `feat(scope): ...`
6. Push: `git push origin <branch>`
7. Finish: merges to `feature/upgrade` (NOT develop), deletes feature branch

### Commit Convention
- `feat(scope):` new feature
- `fix(scope):` bug fix
- `refactor(scope):` refactor
- `test(scope):` tests
- `docs(scope):` documentation
- `chore(scope):` maintenance
- Subject line max 50 chars
- Body explains WHY, not WHAT

## Code Conventions

### Python
- Type hints mandatory (no `Any` unless justified in comment)
- Google-style docstrings (1-line summary; args/returns only if non-obvious)
- Explicit error handling, no bare `except`
- No `print()` — use structlog
- Config via env (Pydantic Settings), no hardcoded values
- Async where existing code is async

### Style
- Use `caveman` skill for all output (token-minimal)
- Use `job_scraper-spec` skill when working on this codebase
- SPEC-driven: write/read SPEC before implementation

## Test/Lint Commands
- Test: `npm test`
- Lint: `ruff check .`

## SPEC Locations
- `docs/specs/`: SPECs per feature
- `docs/runbooks/`: ops runbooks
- `docs/reports/`: discovery + baseline + dry-run reports
- `MEMORY.md`: project memory + decision log + pending work

## Pipeline Architecture (target)
Fetch → Normalize → Pre-filter (no AI) → AI Classify (Groq, single call) → Quality Gate → Persist
Separate expiration job (HTTP probe).

## Models
- Plan/architecture/migration: claude-opus-4-7
- Implementation: claude-sonnet-4-6

## MongoDB
- URI: `mongodb://localhost:27017/devboards`
- Single `jobs` collection with normalized schema (defined in SPECs)
- Wipe & re-import strategy approved for this upgrade

## AI Provider
- Groq with model from `GROQ_MODEL` env (default: llama-3.1-8b-instant)
- Single call per offer with strict JSON schema
- Aggressive pre-filter to save tokens

## Out of Scope (this repo)
- Bun + Elysia API (separate repo, must verify schema contract before migration)
- Qwik frontend
- DevBoards business logic outside import

## Original CLAUDE.md (preserved)
# CLAUDE.md — DevBoards Import Service

## Project Context
Python service that imports job offers from external portals (RSS, APIs, HTML) into MongoDB.
Consumed by Bun + Elysia API and Qwik frontend (out of scope for this repo).

## Active Work Branch
All current work happens on `feature/upgrade`.
New features branch FROM `feature/upgrade` (not develop), and merge back to `feature/upgrade`.

## Git-Flow Conventions

### Branch Strategy
- `master`: production
- `develop`: integration (untouched during this upgrade work)
- `feature/upgrade`: current upgrade work parent
- `feature/claude-XX-name`: per-task branches off `feature/upgrade`

### Per-Feature Workflow (mandatory for every task)
1. Start: `git flow feature start claude-XX-name` (auto from develop, but we override)
2. Implementation per SPEC + skill conventions
3. Run tests: `npm test` — must pass
4. Run lint: `ruff check .` — must pass
5. Commit (conventional commits): `feat(scope): ...`
6. Push: `git push origin <branch>`
7. Finish: merges to `feature/upgrade` (NOT develop), deletes feature branch

### Commit Convention
- `feat(scope):` new feature
- `fix(scope):` bug fix
- `refactor(scope):` refactor
- `test(scope):` tests
- `docs(scope):` documentation
- `chore(scope):` maintenance
- Subject line max 50 chars
- Body explains WHY, not WHAT

## Code Conventions

### Python
- Type hints mandatory (no `Any` unless justified in comment)
- Google-style docstrings (1-line summary; args/returns only if non-obvious)
- Explicit error handling, no bare `except`
- No `print()` — use structlog
- Config via env (Pydantic Settings), no hardcoded values
- Async where existing code is async

### Style
- Use `caveman` skill for all output (token-minimal)
- Use `job_scraper-spec` skill when working on this codebase
- SPEC-driven: write/read SPEC before implementation

## Test/Lint Commands
- Test: `npm test`
- Lint: `ruff check .`

## SPEC Locations
- `docs/specs/`: SPECs per feature
- `docs/runbooks/`: ops runbooks
- `docs/reports/`: discovery + baseline + dry-run reports
- `MEMORY.md`: project memory + decision log + pending work

## Pipeline Architecture (target)
Fetch → Normalize → Pre-filter (no AI) → AI Classify (Groq, single call) → Quality Gate → Persist
Separate expiration job (HTTP probe).

## Models
- Plan/architecture/migration: claude-opus-4-7
- Implementation: claude-sonnet-4-6

## MongoDB
- URI: `mongodb://localhost:27017/devboards`
- Single `jobs` collection with normalized schema (defined in SPECs)
- Wipe & re-import strategy approved for this upgrade

## AI Provider
- Groq with model from `GROQ_MODEL` env (default: llama-3.1-8b-instant)
- Single call per offer with strict JSON schema
- Aggressive pre-filter to save tokens

## Out of Scope (this repo)
- Bun + Elysia API (separate repo, must verify schema contract before migration)
- Qwik frontend
- DevBoards business logic outside import

## Original CLAUDE.md (preserved)
