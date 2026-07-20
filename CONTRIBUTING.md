# Contributing to RMI (Rug Munch Intelligence)

Thank you for your interest in contributing. RMI is a multi-chain crypto intelligence platform
providing real-time scam detection, token security scanning, wallet analysis, and market intelligence
across 96 blockchains.

## Quick Start

1. **Read `AGENTS.md` first** — it contains the architecture rules and development constraints.
2. Clone the repo and set up a virtual environment:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```
3. Run the quality gate to verify your environment:
   ```bash
   make quality
   ```
4. Read the Architecture Decision Records in `docs/adr/` to understand key design decisions.

## Code Standards

See **[`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md)** for the full reference.
Quick summary for contributors:

- **Format:** `ruff format app/`
- **Lint:** `ruff check --fix app/`
- **Types:** `mypy app/core/ app/domains/ --ignore-missing-imports`
- **Security:** `bandit -r app/ -ll`
- **No file > 500 lines** — split god files into domain modules.
- **No `print()` in app code** — use `from app.core.logging import get_logger`
- **No `time.sleep()`** — use `asyncio.sleep()`
- **No sync HTTP** — use `httpx.AsyncClient`
- **Never add to `main.py`** — entry point only. New code goes in `app/domains/` or `app/core/`.

## Architecture Decision Records

Key design decisions are documented in `docs/adr/`:

| ADR | Title | Status |
|-----|-------|--------|
| 001 | DataBus as the Single Data Layer | Accepted |
| 002 | Monolith over Microservices | Accepted |
| 003 | Redis for Hot Cache, Postgres for Cold Storage | Accepted |
| 004 | Multi-Source Data Fusion for Risk Scoring | Accepted |
| 005 | x402 Payment-Gated API Access | Accepted |
| 006 | CI Quality Gates | Accepted |

When making a significant architecture decision, add a new ADR following the existing format.

## Development Flow

1. **Plan:** Review existing ADRs and understand the architecture before coding.
2. **Branch:** `git checkout -b feat/your-change` (use `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`, `test/`).
3. **Code:** Follow `docs/CODING_STANDARDS.md`. Write tests first (TDD preferred for business logic).
4. **Verify:** Run `make quality` — this runs lint + typecheck + security + tests.
5. **Commit:** Use conventional commits (`feat(scope): description`).
6. **PR:** Push and open a PR. CI runs the full quality gate — all checks must pass.

## Quality Gate

Every push is checked by our CI quality gates (see ADR-006):

```
┌─────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐
│ ruff    │  │ mypy     │  │ bandit   │  │ pytest │
│ lint    │  │ typecheck│  │ security │  │ tests  │
└─────────┘  └──────────┘  └──────────┘  └────────┘
     │             │             │             │
     └─────────────┴──────┬──────┴─────────────┘
                          ▼
                 ✅ ALL MUST PASS
```

Run locally with:
```bash
make quality
```

## Where to Start

- Look for issues labeled `good-first-issue` or `help-wanted`.
- Improve test coverage on domain modules.
- Add type hints to untyped public functions.
- Fix lint errors (`ruff check app/`).
- Review `docs/adr/` — are any decisions outdated?
- Improve documentation.

## Code Review Checklist

- [ ] Tests pass (all gates green)
- [ ] Type annotations complete and correct
- [ ] Error handling covers invalid input, external failure, timeout
- [ ] No secrets, API keys, or RPC URLs in code
- [ ] No `print()`, no `time.sleep()`, no f-string logs
- [ ] Async/await used for all I/O
- [ ] File is under 500 lines
- [ ] Docstrings on all public functions
- [ ] CHANGELOG updated if user-facing change
- [ ] ADR updated if architecture decision changed

## Questions

Open a GitHub/Forgejo issue or discussion if unsure. We're happy to help.
