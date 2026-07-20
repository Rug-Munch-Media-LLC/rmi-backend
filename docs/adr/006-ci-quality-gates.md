# ═══════════════════════════════════════════════════════════════
# ADR-006: CI Quality Gates
# ═══════════════════════════════════════════════════════════════
# Date: 2026-07-20
# Status: Accepted

## Context

RMI Backend has accumulated significant technical debt: 8.61% test coverage,
17 `time.sleep()` calls, 399 `print()` statements, 1,713 f-string-in-log
patterns, and CI gates that use `continue-on-error: true` and `|| true`.
Without enforceable quality gates, the codebase will continue to degrade.

The development team operates in an AI-assisted workflow where code is
generated quickly. Automated quality gates are the safety net that catches
common LLM-generated issues (hallucinated imports, type errors, security
anti-patterns) before they hit production.

## Decision

Four quality gates are enforced on every push and every PR, with pre-commit
hooks and GitHub Actions. A gate failure blocks the merge — no exceptions.

### Gate 1: Lint (`ruff check`)

- Rules: E, F, B, SIM, ASYNC, S (security)
- Exemptions: S104 (hardcoded bind), S311 (predictable random) — reviewed
- Runs on: `app/`, `tests/`
- Enforcement: `continue-on-error: false` (blocking)

### Gate 2: Type Check (`mypy --strict`)

- Strict mode on domain code: `app/domains/`, `app/core/`
- `--ignore-missing-imports` on third-party packages without stubs
- Runs on: `app/`
- New code must have complete type annotations

### Gate 3: Security Scan (`bandit` + secret detection)

- `bandit -r app/ -ll` — high-confidence issues only
- Custom secret scanner: rejects commits with `0x[0-9a-fA-F]{64}` (private keys)
  or `sk-[a-zA-Z0-9]{20,}` (OpenAI keys)
- Runs on: `app/`, `scripts/`

### Gate 4: Tests (`pytest`)

- Minimum pass rate: 100% (zero tolerance for failing tests)
- Coverage threshold: 15% (current baseline), ratcheting up 2%/month
- Unit tests only in CI (integration tests tagged `@pytest.mark.integration`)
- New features require tests for: happy path, error path, edge cases

### Pre-commit Hook (local)

Runs before every commit via lefthook:

```yaml
pre-commit:
  parallel: true
  commands:
    ruff:
      run: ruff check --select=E,F,B,SIM,ASYNC {staged_files}
    mypy:
      run: mypy {staged_files}
    secrets:
      run: bash scripts/scan-secrets.sh
    format:
      run: ruff format --check {staged_files}
```

### CI Pipeline (GitHub Actions)

```yaml
jobs:
  quality:
    steps:
      - lint (ruff)
      - typecheck (mypy)
      - security (bandit + secrets)
      - test (pytest --cov)
```

All gates run in parallel. If any fails, the pipeline is red.
The PR cannot be merged until all gates pass.

## Migration Path

Current `continue-on-error: true` and `|| true` patterns are removed
immediately. Files that don't pass are added to a lint baseline
(`ruff check --exit-zero`) with a 30-day SLA to fix. After 30 days,
the baseline is removed.

## Alternatives Considered

1. **No gates** — rejected: codebase is already degrading; without
   enforcement it will continue
2. **Only CI, no pre-commit** — rejected: feedback loop is too slow;
   developers (especially AI agents) need instant feedback
3. **Softer gates (warnings only)** — rejected: warnings are ignored;
   only hard failures drive compliance

## Consequences

- Initial CI will likely fail — expect a burst of fixes in the first week
- AI agents must run `make quality` before creating any PR
- `continue-on-error: true` is permanently banned from CI config
- Coverage will improve monotonically (ratchet only goes up)
- Gate exemptions are reviewed quarterly; any exemption older than
  90 days is automatically revoked
