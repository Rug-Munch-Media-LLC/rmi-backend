# RMI Backend — 2026 Architecture Design

## Why this exists

The current backend has grown organically:
- `main.py` is 10,305 lines
- 124 router files flat in `app/routers/`
- 14 RAG modules scattered at top of `app/`
- `token_scanner.py` is 4,109 lines
- `x402_tools.py` is 5,817 lines
- Cross-cutting concerns (redis, auth, errors) duplicated across modules
- Domain logic entangled with FastAPI
- No tests, no type safety, no clear boundaries

15 mechanical refactor tasks would patch symptoms. This design fixes the architecture.

## Target Layout

```
backend/
├── pyproject.toml              uv + ruff + mypy + pytest config (single source)
├── .pre-commit-config.yaml     ruff + mypy + size cap + gitleaks
├── Dockerfile
├── alembic/                    async migrations
│
├── app/
│   ├── main.py                 <100 lines: app factory + lifespan + middleware ONLY
│   ├── config.py               pydantic-settings, env loading
│   │
│   ├── core/                   cross-cutting, NO business logic
│   │   ├── logging.py          structlog JSON + correlation ID
│   │   ├── errors.py           AppError hierarchy + FastAPI handlers
│   │   ├── redis.py            async client + get_redis() Depends()
│   │   ├── db.py               async SQLAlchemy session
│   │   ├── auth.py             JWT decode + role guards
│   │   ├── lifespan.py         startup/shutdown
│   │   ├── middleware.py       CORS, rate limit, correlation ID
│   │   ├── websocket.py        WS connection manager
│   │   ├── tracing.py          OpenTelemetry + Langfuse v4 init
│   │   ├── http.py             async httpx client
│   │   └── pagination.py       cursor-based
│   │
│   ├── api/                    HTTP transport, thin routes
│   │   ├── deps.py             shared Depends (current_user, redis, etc)
│   │   ├── v1/
│   │   │   ├── public/         no auth  — scanner, wallet, token, pricing, health
│   │   │   ├── auth/           JWT      — portfolio, alerts, intel, profile
│   │   │   ├── admin/          admin    — users, system, ops
│   │   │   ├── x402/           paid     — tools, tokens, wallets, defi, security
│   │   │   └── mcp/            MCP      — tools.py
│   │   └── ws/                 WebSocket
│   │       └── alerts.py
│   │
│   ├── domain/                 pure business logic, NO FastAPI imports
│   │   ├── scanner/            core + honeypot + rugcheck + holders + contract + deployer + models + service
│   │   ├── wallet/             analyzer + labels + behavior + models + service
│   │   ├── token/              discovery + supply + models + service
│   │   ├── rag/                embeddings + chunking + search + ingest + firehose + feedback + agentic + evaluation + tracing + router + permanence + models + service
│   │   ├── x402/               facilitator + tokens + enforcement + settlement + models + service
│   │   ├── intel/              feeds + narratives + graph + models + service
│   │   ├── scam/               classifier + patterns + models + service
│   │   ├── databus/            client + chains(96) + models + service
│   │   └── bulletin/           board + models + service
│   │
│   ├── infra/                  external integrations
│   │   ├── langfuse.py
│   │   ├── vector_store.py
│   │   ├── chains/             evm + solana + bitcoin + base + ...
│   │   ├── apis/               coingecko + etherscan + birdeye + goplus + ...
│   │   └── providers/          openrouter + huggingface + ...
│   │
│   └── workers/                background jobs (separate from API)
│       ├── firehose.py
│       ├── scanner_queue.py
│       ├── ingest_cron.py
│       └── cleanup.py
│
└── tests/
    ├── conftest.py
    ├── unit/domain/
    └── integration/api/v1/
```

## Key Design Principles

1. **STRICT LAYERING.** `api → domain → infra`. Never reverse. Domain knows nothing about HTTP.
2. **ONE SOURCE OF TRUTH for cross-cutting.** redis/auth/errors/logging live in `core/` exactly once. Routes import, never redefine.
3. **HARD SIZE CAP.** 500 lines per file. Enforced in pre-commit. No 4,109-line `token_scanner.py` ever again.
4. **THIN ROUTES.** Routes parse → call service → return. No business logic in HTTP layer.
5. **DOMAIN = PURE PYTHON.** `domain/scanner/` can be unit tested without spinning up FastAPI. This is the test that proves the architecture.
6. **WORKERS SEPARATED.** Background jobs don't pollute the API. firehose, scanner_queue, ingest_cron live in `workers/`.
7. **PYDANTIC V2 EVERYWHERE.** Every domain has `models.py`. No `dict` types crossing boundaries.
8. **ASYNC-ONLY.** No sync I/O in handlers. Same shape for the whole codebase.
9. **OBSERVABILITY BY DEFAULT.** structlog JSON + correlation ID + OTel + Langfuse in `core/tracing.py`. Every endpoint instrumented without opt-in.
10. **STRANGLER FIG MIGRATION.** New skeleton co-exists with old code. Old `main.py` keeps importing the old routers. New routes added alongside. Per-domain cutover, not big-bang.

## Migration Order

| Order | Domain | Why |
|-------|--------|-----|
| 0 | `rag_engine` shim | unblock prod crash, temp until `app/rag/` lands |
| 1 | `core/` | foundation everyone depends on |
| 2 | `infra/` | external integrations domain depends on |
| 3 | `alerts` | smallest, well-bounded, has WS + JWT + redis — proves full pattern |
| 4 | `wallet` | high-value, used by frontend |
| 5 | `token` | high-value |
| 6 | `scanner` | biggest (4,109 lines), do last when pattern is mature |
| 7 | `x402` | payment system, critical, mature pattern by then |
| 8 | `intel`, `scam`, `databus`, `bulletin` | long tail |
| 9 | `rag` consolidation (was 14 files) | last because it's the most coupled |

## What Ships This Pass (Foundation)

1. Fix crash — `rag_engine` re-export shim, backend healthy
2. `pyproject.toml` — uv + ruff + mypy strict + pytest
3. `.pre-commit-config.yaml` — ruff + mypy + size cap (500) + gitleaks
4. `app/core/` — 11 modules, each <200 lines
5. `app/api/v1/__init__.py` — router aggregator that still imports OLD routers (zero breakage)
6. `app/main.py` — rewritten to ~100 lines, calls lifespan + middleware from `core/`, mounts new aggregator
7. Verify: backend boots, all 757 routes respond, health 200, no import errors
8. Commit + deploy

## What Does NOT Ship This Pass

- Migrating alerts/wallet/token/scanner to new `domain/`. That's Phase 2.
- The 15 mechanical refactors. Replaced with the layered architecture.
- Deleting old code. Strangler fig — old stays until domain is migrated.

## Phase 2: Alerts Vertical Slice (proves the pattern)

After foundation lands, migrate `alerts` end-to-end as the reference:

```
app/domain/alerts/
├── models.py      # Alert, AlertRule, Notification — Pydantic v2
├── repository.py  # async SQLAlchemy queries
├── service.py     # business logic, pure Python
└── broadcaster.py # WebSocket broadcast helper

app/api/v1/auth/alerts.py  # thin route: parse → call service → return
```

This proves the pattern works: domain is pure Python, route is <100 lines, can be unit tested without HTTP.

When alerts is shipped and verified in prod, the same pattern is applied to wallet, token, scanner, etc.
