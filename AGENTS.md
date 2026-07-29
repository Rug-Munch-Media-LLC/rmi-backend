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
# Last updated: 2026-07-29

## What This Is

rmi-backend — FastAPI crypto intelligence backend (v2026.06.21). 13+ chains,
96 data providers, 8 MCP tools, x402 paid tier, sovereign-first FOSS.
Routes, MCP tools, DataBus chains, wallet labels, scanner modules,
domain-driven architecture.

## Architecture

```
app/
├── factory.py             # FastAPI app factory (single composition point)
├── main.py                # Entrypoint — delegates to create_app()
├── mount.py               # Single source of truth for ALL router mounts
├── lifespan.py            # Startup/shutdown lifecycle
├── middleware_setup.py    # Middleware registration
├── error_handlers.py      # Exception handlers (RFC 7807 problem details)
├── config.py              # Pydantic Settings (env-based config)
│
├── api/v1/                # v1 API surface (strangler fig — all new routes)
│   ├── admin/             # Admin endpoints
│   ├── auth/              # Authentication
│   ├── mcp/               # MCP tool endpoints
│   ├── x402/              # Paid x402 tier
│   ├── rag/               # RAG query endpoints
│   ├── catalog/           # Tool catalog
│   └── internal/          # Internal health/debug
│
├── routers/               # 73 router modules (60+ endpoint groups)
│   ├── x402_tools.py      # x402 paid tool endpoints
│   ├── unified_scanner_router.py
│   ├── market_intel_router.py
│   ├── intelligence_router.py
│   ├── token_intelligence_v2.py
│   └── ... (68 more)
│
├── domains/               # Domain-driven modules (26 domains)
│   ├── admin/             # Admin panel domain
│   ├── auth/              # Wallet auth, JWT
│   ├── billing/           # Stripe billing
│   ├── databus/           # DataBus chain registry
│   ├── detectors/         # Detection engine modules
│   ├── labels/            # Wallet labeling
│   ├── news/              # News domain
│   ├── newsletter/        # Email newsletter
│   ├── portfolio/         # Portfolio tracking
│   ├── prediction/        # Prediction markets
│   ├── scanner/           # Scanner engine modules
│   ├── telegram/          # Telegram bot
│   ├── threat/            # Threat intel
│   ├── token/             # Token analysis
│   ├── wallet/            # Wallet analysis
│   └── x402/              # x402 payment gateway
│
├── databus/               # Unified data provider layer (52 modules)
│   ├── providers.py       # 50+ provider integrations
│   ├── core.py            # Core DataBus engine
│   ├── cache.py           # Redis-backed caching
│   ├── vault.py           # Secrets management
│   └── provider_chains.py # Chain-to-provider mapping
│
├── scanners/              # Scanner pipeline modules (5 core)
│   ├── insider_detector.py
│   ├── gmgn_client.py
│   ├── known_entities.py
│   ├── intel_aggregation.py
│   └── data_fallback.py
│
├── temporal/              # Temporal workflow engine
│   ├── workflows.py
│   ├── activities.py
│   └── worker.py
│
├── observability/         # OpenTelemetry + error tracking
│   ├── tracing.py
│   ├── error_tracking.py
│   ├── langfuse_tracer.py
│   └── spans.py
│
├── rag/                   # RAG pipeline (ChromaDB, hybrid search, reranking)
├── agents/                # AI agent system
├── auth/                  # Auth helpers
├── models/                # SQLAlchemy ORM models
├── mcp/                   # MCP tool system
├── core/                  # Cross-cutting utilities
├── billing/               # x402 payment billing
├── services/              # Business logic services
├── security/              # Security helpers
└── workers/               # Background workers
```

### Services Stack
| Service | Tech | Purpose |
|---------|------|---------|
| API | FastAPI + Uvicorn/Gunicorn | HTTP API |
| Cache | Redis 7 (alpine) | Session, rate limiting, cache |
| Primary DB | PostgreSQL 16 (alpine) | ORM data |
| Analytics | ClickHouse 24 (alpine) | Time-series events |
| Graph | Neo4j 5 (community) | Entity graph |
| Vectors | Qdrant v1.12 | Embedding search |
| Object Store | MinIO | File/artifact storage |
| Streaming | Redpanda | Event streaming |
| Workflows | Temporal | Long-running workflows |
| Observability | OpenTelemetry + GlitchTip | Traces + errors |
| AI Tracing | Langfuse | LLM observability |

### Key Route Groups
| Group | Path | Description |
|-------|------|-------------|
| Health | `GET /live`, `/ready`, `/health` | Liveness, readiness, full health |
| MCP | `GET /mcp/tools`, `POST /mcp/call/{id}` | MCP tool discovery + execution |
| v1 API | `/api/v1/*` | Current API surface (strangler fig) |
| x402 | `/api/v1/x402/*` | Paid tier endpoints |
| Databus | `/api/v1/databus/*` | Data chain queries |
| WebSocket | `/ws/*` | Real-time alerts |

### Build & Deploy
- Python 3.12-slim (Docker), pip requirements, gunicorn 4 workers
- Docker Compose with 5-layer startup: DNS → DBs → Support → App → Monitoring
- Multi-stage Dockerfile with tini init, read-only rootfs, dropped capabilities

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
pytest -v
pytest tests/unit/test_X.py -v
pytest --cov=app --cov-fail-under=60  # coverage gate
pytest -m "not slow"                   # skip slow tests
```

## Linting

```
ruff check app/ --fix
ruff format app/
mypy app/ --strict
```

## MCP Tools

210+ MCP tools organized by category: token analysis, wallet intel,
scam detection, market data, news intelligence, RAG queries,
portfolio tracking, and x402 paid marketplace tools.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp/tools` | GET | List all available MCP tools |
| `/mcp/call/{tool_id}` | POST | Execute an MCP tool |
| `/mcp/info` | GET | MCP system info and health |

Tool system in `app/mcp/` and `app/routers/mcp_server/`.

## Scanner Architecture

Two scanner locations serve different purposes:

**`app/scanners/`** — Core scanner pipeline (5 modules):
- `insider_detector.py` — Insider trading detection
- `gmgn_client.py` — GMGN data integration
- `known_entities.py` — Known entity matching
- `intel_aggregation.py` — Multi-source intel aggregation
- `data_fallback.py` — Graceful data source degradation

**`app/domains/scanner/`** — Domain-driven scanner engine:
- `unified_token_scanner.py` — Main token scan orchestrator
- `degen_security_scanner/` — Security scan modules
- `rugmaps/` — RugMaps integration
- `engines/` — Detection engine implementations
- `service/` — Scanner business logic

## Adding a New Scanner Module

1. Create in `app/scanners/` (core pipeline) or `app/domains/scanner/` (domain scanner)
2. Register in appropriate `__init__.py`
3. Add router endpoint in `app/routers/unified_scanner_router.py` or domain router
4. Add pricing to `app/routers/x402_enforcement.py` if paid

CRITICAL: Never call our own x402 endpoints internally.
CRITICAL: Scanner modules must call providers through DataBus, never directly.

## Adding a New DataBus Chain

1. Create provider in `app/databus/providers/`
2. Register in `app/databus/provider_chains.py`
3. Register provider class in `app/databus/providers.py`
4. If chain provides news/intel, register in `app/databus/news_provider.py`

Currently 50+ providers across 52 DataBus modules.

## Rules

1. All data through DataBus — never call providers directly
2. Type everything — Pydantic v2 + mypy strict
3. Async for all I/O — httpx.AsyncClient, asyncpg, asyncio
4. Never bare except — always log + re-raise
5. Use env vars — never hardcode hosts or secrets
6. Add tests for new code — coverage > 60% (gate), aim for > 80%
7. Conventional commits — feat:/fix:/chore:/docs:/refactor:/test:/security:/perf:
8. No secrets in git — vault.py + gopass is the only storage
9. No circular API calls — scanner modules don't call our own endpoints
10. Don't add to main.py (30 lines, by design) — use app/factory.py + app/mount.py
11. New routes go in /api/v1/* (strangler fig pattern). Root-level routes deprecated.
12. Domains in app/domains/ — isolated, no FastAPI leak into domain logic
13. Observability: OpenTelemetry traces + Langfuse LLM tracing — enable for new modules

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
