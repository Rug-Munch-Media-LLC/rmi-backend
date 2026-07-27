## 🚨 CRITICAL RULES (enforced by gitleaks pre-commit hook)

1. **No nested repos.** Don't commit other complete project trees inside this one.
2. **No secrets.** Never commit `.secrets/`, `.env`, `*.pem`, `*.key`, `*.crt`, cookie jars.
3. **No data blobs.** Don't commit `*.zip`, `*.parquet`, model weights, sqlite files.
4. **No broken files.** Shell heredoc accidents must be caught before commit.
5. **No duplicate scaffolds.** If a `backend/` or `frontend/` subdir exists at root,
   it's either THE app or bloat — investigate before adding more.

See [CLEANUP.md](CLEANUP.md) for cleanup history.

---

# AGENTS.md — rmi-backend
# Every AI agent working on backend code reads this first.
# Last updated: 2026-06-29

## What This Is

rmi-backend — FastAPI crypto intelligence backend. Routes, MCP tools,
DataBus chains, wallet labels, scanner modules.

## Before You Start

Read these files in order:
  1. ~/AGENTS.md          — fleet-wide contract, product table, git policy
  2. ~/STYLE.md           — coding conventions and patterns
  3. ./AGENTS.md (this)   — backend-specific instructions
  4. ./README.md          — full project overview
  5. ~/STANDARDS.md       — linting, testing, quality gates
  6. ~/TOOLS.md           — developer tool inventory

## Development Workflow

Code in the backend directory is Docker volume-mounted — changes go live on restart.

## Testing

```
pytest -p no:pytest-brownie -v
pytest tests/unit/test_X.py -p no:pytest-brownie -v
```

The `-p no:pytest-brownie` is mandatory — eth-brownie tries to connect to ganache.

## Linting

```
ruff check app/ --fix
ruff format app/
mypy app/ --strict
```

## MCP Tools

mcp_discover, status_check, analytics_query, wallet_labels,
eth_labels_tool, token_security, scam_intel, news_search,
rag_query, report_generate, x402_marketplace (+ 210 more)

List: GET /mcp/tools  Call: POST /mcp/call/{tool_id}  Info: GET /mcp/info

## Adding a New Scanner Module

1. Create app/scanners/my_detector.py (fetches from first-party APIs only)
2. Add import to app/scanners/__init__.py
3. Add runner function in app/scanners/sentinel_pipeline.py
4. Add endpoint to app/routers/x402_tools.py
5. Add pricing to x402_enforcement.py

CRITICAL: Never call our own x402 endpoints internally.

## Adding a New DataBus Chain

1. Create provider in app/databus/providers/
2. Register in app/databus/chains.py
3. Register provider class in app/databus/registry.py

## Rules

1. All data through DataBus — never call providers directly
2. Type everything — Pydantic v2 + mypy strict
3. Async for all I/O — httpx.AsyncClient, asyncpg, asyncio
4. Never bare except — always log + re-raise
5. Use env vars — never hardcode hosts or secrets
6. Add tests for new code — coverage > 80%
7. Conventional commits — feat:/fix:/chore:/docs:/refactor:/test:
8. No secrets in git — vault.py is the only storage
9. No circular API calls — scanner modules don't call our own endpoints
10. Don't add to main.py (28 lines, by design) — use app/mount.py

## Open-Core Protection

This repo follows the open-core model. Scanner pipelines, API patterns, and
database schemas visible in this repo are open source. The core admin backend,
proprietary scoring algorithms, and production deployment configurations remain
private as part of the RugMunch managed platform at rugmunch.io.

See [CONTRIBUTING.md](CONTRIBUTING.md) for project methodology.
for details on what information belongs in public vs private repos.

## Git

- Classification: INTERNAL (core) + OSS (scanners)
- Commits: type(scope): description
- Never commit: .env, secrets, internal IPs, security reports
