# RMI Coding Standards

> **Audience:** All developers writing code for RMI Backend and related services.
> **Companion docs:** `STANDARDS.md` (ops/infra), `AGENTS.md` (AI agent rules),
> `docs/adr/` (architecture decisions).

---

## Python Standards

### Type Hints

- **Required** on all public function signatures (parameters + return type)
- Use `from __future__ import annotations` to enable PEP 604 syntax
- Prefer `|` over `Optional[]`: `str | None` not `Optional[str]`
- Use `TypeVar` for generic functions, `Protocol` for duck typing
- MyPy strict mode on `app/domains/` and `app/core/`

```python
# ✅ Good
from __future__ import annotations

def scan_token(address: str, chain: str = "ethereum") -> ScanResult | None:
    ...

# ❌ Bad
def scan_token(address, chain="ethereum"):
    ...
```

### Docstrings

- Google-style with `Args:`, `Returns:`, `Raises:` sections
- Required on: all public modules, classes, and functions
- One-line docstrings OK for obvious getters/setters

```python
def fetch_token_price(mint: str) -> PriceData | None:
    """Fetch current USD price for a token mint.

    Args:
        mint: Solana token mint address.

    Returns:
        PriceData with usd_price and 24h_change, or None if unknown.

    Raises:
        DataBusError: If all provider chains are exhausted.
    """
```

### Async/IO

- All I/O operations must use `async/await`
- Use `httpx.AsyncClient` for HTTP — never `requests`
- Use `async def` for all route handlers, DataBus calls, DB queries
- Use `asyncio.gather()` for parallel independent operations
- Never `time.sleep()` — use `asyncio.sleep()`

```python
# ✅ Good
async def get_prices(tokens: list[str]) -> dict[str, float]:
    async with httpx.AsyncClient() as client:
        tasks = [fetch_price(client, t) for t in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return {t: r for t, r in zip(tokens, results) if not isinstance(r, Exception)}

# ❌ Bad
def get_prices(tokens):
    time.sleep(0.5)  # blocking!
    return requests.post("https://api.example.com/prices", json=tokens).json()
```

### Logging

- Use `from app.core.logging import get_logger`
- Never f-string in log calls — use `%s` formatting
- Never `print()` in app code

```python
log = get_logger(__name__)
log.info("Scanning token %s on chain %s", address, chain)  # ✅
log.info(f"Scanning token {address}")                       # ❌
print(f"Scanning token {address}")                          # ❌
```

### Pydantic

- All API request/response schemas use Pydantic v2 models
- Use `model_validator` for cross-field validation
- Use `field_validator` for per-field normalization
- Prefer `str` over `Any` — type everything

### File Structure

- Max 500 lines per file. Split god files into domain modules.
- One class per file (plus small helpers)
- Module `__init__.py` exports only the public API

---

## Testing Standards

### Framework

- pytest for Python (no unittest)
- pytest-asyncio for async tests
- pytest-cov for coverage

### Test Structure

```
tests/
├── unit/           # Pure unit tests, no external deps
│   ├── domains/
│   └── core/
├── integration/    # Tests with real DB/API (tagged @pytest.mark.integration)
└── conftest.py     # Shared fixtures
```

### Requirements

- New features: happy path + error path + edge case tests
- Mock all external APIs with `respx` (HTTP) or `unittest.mock`
- Test business logic, not framework behavior
- Minimum 80% pass rate (100% = zero tolerance for failures)
- Coverage trending toward 30% across the codebase

### Fixtures

```python
@pytest_asyncio.fixture
async def mock_databus():
    """Mock DataBus that returns canned token data."""
    with patch("app.domains.databus.service.DataBus.fetch") as mock:
        mock.return_value = {"price": 1.23, "risk": "low"}
        yield mock
```

---

## Git Standards

### Conventional Commits

```
feat(scanner): add Solana token holder analysis
fix(databus): handle rate limit retry correctly
chore(deps): update httpx to 0.28.0
docs(adr): add ADR-005 for x402 payments
refactor(fusion): simplify weight calculation
test(scanner): add edge case for empty holders
perf(cache): reduce Redis TTL for price data
```

### Branch Names

- `feat/` — new features
- `fix/` — bug fixes
- `chore/` — maintenance, deps, CI
- `docs/` — documentation only
- `refactor/` — code restructuring
- `test/` — test additions/fixes

### Commit Rules

- No `--no-verify` (don't skip hooks)
- No direct pushes to `main` — always PR
- Sign commits with GPG when possible

---

## Code Review Checklist

- [ ] Tests pass (all gates green)
- [ ] Type annotations are complete and correct
- [ ] Error handling covers: invalid input, external failure, timeout
- [ ] No secrets, API keys, or RPC URLs in code
- [ ] No `print()`, no `time.sleep()`, no f-string logs
- [ ] Async/await used for all I/O
- [ ] File is under 500 lines
- [ ] Docstrings on all public functions
- [ ] CHANGELOG updated if user-facing change
- [ ] ADR updated if architecture decision changed

---

## Enforcement

| Gate | Tool | Enforced By |
|------|------|-------------|
| Format | `ruff format` | Pre-commit + CI |
| Lint | `ruff check` | Pre-commit + CI |
| Types | `mypy --strict` | Pre-commit + CI |
| Security | `bandit` + secrets scan | Pre-commit + CI |
| Tests | `pytest --cov` | CI |
| Coverage | `pytest --cov-fail-under` | CI (ratcheting) |

Run `make quality` locally before any commit or PR.
