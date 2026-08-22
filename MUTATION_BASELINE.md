# Mutation Testing Baseline — rmi-backend

**Tool:** mutmut 3.6.0 · **Scope:** `app/domains/auth` (critical path #1) · **Recorded:** 2026-08-22

## Baseline: auth domain — 50%

| Metric | Value |
|---|---|
| Mutants generated | 1,124 |
| Killed | 72 |
| Survived | 72 |
| No tests | 980 |
| **Score (covered)** | **50%** |

### Reading
- `tests/unit/domain/auth` covers jwt + passwords well (~50% of covered mutants killed)
- **980 no-test mutants** live in oauth / totp / wallet / store / routes_* — these
  modules have no dedicated unit tests yet ← biggest coverage gap in the auth domain
- Gate: score must not regress >5 points; target ≥80% on covered set within 14 days,
  then expand test selection (`tests/unit/domain/auth` → add oauth/totp suites)

## Config (pyproject.toml)

```toml
[tool.mutmut]
source_paths = ["app/domains/auth"]
also_copy = ["app"]   # full tree: auth transitively imports app.core -> app.routers.*
pytest_add_cli_args_test_selection = ["tests/unit/domain/auth"]
```

### Sandbox lessons (backend-specific)
- `also_copy = ["app"]` + scoped `source_paths` is THE pattern for entangled
  trees: full import graph present, only auth mutated. Partial also_copy lists
  whack-a-mole forever (app.routers.chat → app.ai_router → …).
- Uncovered mutants skip at ~11/sec; whole-package copy costs nothing at runtime.

## Next targets (priority order)
1. oauth.py + totp.py unit suites (security-critical, currently zero direct tests)
2. store.py session storage tests
3. routes_email / routes_telegram / routes_profile endpoint tests
4. Then widen scope: billing/x402 domain (payments = critical path #2)
