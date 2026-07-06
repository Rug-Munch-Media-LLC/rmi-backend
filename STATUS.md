# STATUS -- rmi-backend

**Owner:** Rug Munch Media LLC Engineering
**Last updated:** 2026-07-06 -- Phase 1 of [AUDIT-2026-Q3.md](AUDIT-2026-Q3.md) complete.

## What This Repo Is

`rmi-backend` -- Rugmuncher Intel FastAPI backend.

Stack: Python 3.12 / FastAPI / SQLAlchemy / async / Playwright / Pydantic v2 / Redis / Vectis / ClickHouse.

643 .py files, 241k LOC under `app/`. Production deploy on Talos: `/srv/rmi-infra/`.

## Current Status

**Phase 1 (development hygiene) complete.** Phase 2 (delete dead code) starts next per [AUDIT-2026-Q3.md](AUDIT-2026-Q3.md).

### Phase 1 -- DONE

Committed to `main`:
- `e404e90` -- `app/main.py` entrypoint delegating to factory
- `c1d157a` -- `tests/` package (`__init__.py` everywhere)
- `da2696a` -- `pyproject.toml:tool.setuptools.packages.find` makes `app` importable
- `e0d0ae3` -- `main.py.bak` `.gitignore`d + removed from index
- `9c62549` -- `JWT_SECRET` fail-fast in prod (`Field(...)` no default + validator)
- `f4d4276` -- `ruff.toml` deleted (pyproject canonical)
- `13255d6` -- `.github/workflows/ci.yml`: 2 gating jobs (build + test), 6 informational
- `7a9043f` -- `pre-commit install` wired into `make install`
- `e8f9b09` -- `tests/test_rag.py` + `run_tests.py` moved to `tests/manual/`
- `92a01ff` + `5294983` -- `AppError` hierarchy in `app/core/errors.py`, wired through `error_handlers.py`
- `cd02714` + `2fb571e` -- author amend (canonical identity: `cryptorugmunch <admin@rugmunch.io>`)

### What Phase 1 DID NOT do (deferred)

- ~2K ruff warnings in legacy code (Phase 5)
- Real coverage 8.17% -> 80% gate (Phase 5, slowest part)
- `mypy.ini` line 8 parse error + `disallow_any_express_imports` typo (Phase 5)
- Alembic migrations (still MISSING; Phase 5)
- 285 dead modules (Phase 2)
- 148 unmounted routers (Phase 2)
- God-files split (Phase 3)
- Domain consolidation (Phase 4)

### Open Issues

_(None blocking deploy. Track via Forgejo issues as they arise.)_

### Recent Activity (last 12 commits)

```
cd02714 test(rmi-backend,audit): move tests/test_rag.py to tests/manual/ (P1.9)
2fb571e build(rmi-backend,audit): install pre-commit hooks + wire into make install (P1.7)
13255d6 ci(rmi-backend,audit): split gating vs informational jobs (P1.6)
5294983 refactor(rmi-backend,audit): wire error_handlers.py to AppError at import time (P1.10 wiring)
92a01ff feat(rmi-backend,audit): AppError hierarchy in app/core/errors.py (P1.10)
da2696a build(rmi-backend,audit): declare app/ as installable Python package (P1.3)
9c62549 fix(rmi-backend,audit): jwt_secret is required, fail-fast in prod (P1.5)
f4d4276 chore(rmi-backend,audit): delete ruff.toml -- pyproject.toml [tool.ruff] is canonical (P1.8)
e0d0ae3 chore(rmi-backend,audit): gitignore main.py.bak (P1.4)
c1d157a test(rmi-backend,audit): add tests __init__.py for package discovery (P1.2)
e404e90 feat(rmi-backend,audit): add app/main.py entrypoint delegating to factory (P1.1)
1c815c0 docs(audit): add 2026-Q3 audit + 6-phase production-grade roadmap (master ref for rmi-backend/frontend)
```

### Security

- `JWT_SECRET` mandatory in prod (`Field(...)` no default + validator catching dev secret). Fail-fast verified across 5 scenarios.
- `gitleaks detect` reports 28 findings -- all **public ERC-20/Solana token contract addresses** (USDC, WETH, MATIC, BONK, DAI, plus cryptoscam-address dataset). NOT secrets. Suppressed via `.gitleaksignore` + `.gitleaks.toml` `[allowlist]`. No rotation needed.
- All secrets live in gopass under `rmi/contacts/admin_recovery` etc.

## Next: Phase 2 (week 2)

Archive **148 unmounted routers** + **~140 dead `app/*.py` files** to `app/_archive/legacy_2026_07/`. Goal: ~30% LOC reduction without changing any current behavior.

See [AUDIT-2026-Q3.md](AUDIT-2026-Q3.md) for the full 6-phase plan.
