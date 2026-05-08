---
name: git-flow-feature
description: Per-feature git-flow workflow for DevBoards upgrade work. Apply when implementing any task.
---

# Git-Flow Per-Feature Workflow

## Parent Branch
All features branch FROM `feature/upgrade` and merge BACK to it (not develop).

## Workflow Steps

### 1. Start Feature
```bash
# verify on feature/upgrade
git checkout feature/upgrade
git pull --ff-only origin feature/upgrade 2>/dev/null || true

# create feature branch directly (do NOT use 'git flow feature start' since it branches from develop)
git checkout -b feature/claude-XX-short-name feature/upgrade
```

### 2. Implement
- Follow SPEC + job_scraper-spec skill
- Write tests first
- Code minimal to pass

### 3. Validate (BLOCKING — must pass before commit)
```bash
# tests
npm test

# lint
ruff check .
```

If either fails: fix before proceeding. NEVER commit broken code.

### 4. Commit
```bash
git add -A
git commit -m "feat(scope): summary

Body explains WHY.
"
```

### 5. Push
```bash
git push -u origin feature/claude-XX-short-name
```

### 6. Finish (merge back to feature/upgrade, delete branch)
```bash
git checkout feature/upgrade
git merge --no-ff feature/claude-XX-short-name -m "merge: feature/claude-XX-short-name"
git push origin feature/upgrade 2>/dev/null || true
git branch -d feature/claude-XX-short-name
git push origin --delete feature/claude-XX-short-name 2>/dev/null || true
```

## Failure Handling
- Tests fail → fix, do not skip
- Lint fails → fix, do not bypass
- Merge conflict on finish → resolve manually, abort feature finish, ask user
- Push fails → continue locally, log warning

## Branch Naming
- `feature/claude-00-discovery`
- `feature/claude-01-architecture-decisions`
- `feature/claude-02-test-scaffolding`
- etc.

Two-digit prefix matches prompt number for traceability.
