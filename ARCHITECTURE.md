# RMI Backend — Architecture

**Status:** canonical
**Last updated:** 2026-07-14

## Overview
FastAPI monolith (strangler fig pattern) serving crypto risk intelligence via HTTP, Telegram, and MCP. Multi-database: Postgres, ClickHouse, Redis, Qdrant, Neo4j, DuckDB.

## App Factory (`app/factory.py` + `app/mount.py`)
```
create_app() → FastAPI instance
  ├── lifespan (app/core/lifespan.py) — startup/shutdown hooks
  ├── error handlers (app/core/errors.py)
  ├── middleware (app/core/middleware.py + app/middleware/)
  └── mount_all() (app/mount.py) — 53 router modules
```
`mount.py` is the single source of truth for every router. Adding a domain: create the module with `router = APIRouter(...)`, add the import path to `ROUTER_MODULES`, done.

## Domain-Driven Layout (`app/domains/`)
| Domain | Purpose |
|--------|---------|
| `alerts/` | Real-time alert broadcast via WebSocket |
| `auth/` | JWT, OAuth, Web3 wallet auth, TOTP |
| `billing/` | x402 payment facilitators, enforcement |
| `bulletin/` | Supabase-backed bulletin system |
| `databus/` | DataBus domain logic (migrating from `app/databus/`) |
| `labels/` | Federated label sources, router |
| `markets/` | Market data domain |
| `mcp/` | MCP server domain |
| `news/` | News aggregation, admin, clusterer |
| `reports/` | AI report generation |
| `referral/` | Referral tracking |
| `scanner/` + `scanners/` | Token risk scanners (migrating from `app/scanners/`) |
| `telegram/` | Telegram bot (@rugmunchbot) |
| `threat/` | CertStream listener, threat intel |
| `token/` + `tokens/` | Token analysis, deployer |
| `wallet/` | Wallet management |
| `x402/` | x402 payment-gated API middleware |

## DataBus Architecture (`app/databus/`)
96 data providers behind a unified `DataProvider` ABC (`app/databus/core.py`). Routes through `app/databus/router.py` with access control, caching, and provider health monitoring.

**Provider layers:**
- `app/databus/providers/` — EVM, Solana, BTC, free tier, paid tier, MCP servers
- `app/databus/` — 30+ files: TokenSecurity, DefiLlama, Birdeye, NewsIntel, x402, social feeds, OHLCV, DuckDB analytics, webhooks, WS streams

**Migration in progress:** Provider chains, implementations, and security are moving to `app/domains/databus/`.

## Router surface (53 wired, app/mount.py)
| Source | Routes |
|--------|--------|
| `app/core/health_route` | /health, /live, /ready |
| `app/core/metrics` | /metrics |
| `app/homepage` | /, /version |
| `app/api/v1/` | auth alerts, wallet, token, scanner, RAG, admin, catalog, MCP |
| `app/domains/` | news, reports, x402 |
| `app/routers/` | 36 wired routers (x402 system, Wave 1/2/3 routers) |

86 dead routers removed 2026-07-14 (0 imports, never wired).

## Key services
| Library | Use |
|---------|-----|
| **FastAPI** | HTTP framework |
| **SQLAlchemy 2.0** | Postgres ORM |
| **Celery + Redis** | Background task queue |
| **httpx** | Async HTTP to 30+ third-party APIs |
| **python-telegram-bot** | Telegram bot framework |

## Storage
| Store | Use |
|-------|-----|
| Postgres (`rmi-postgres`) | Primary OLTP, Ponder indexer data |
| Redis (`rmi-redis`) | Cache, Celery queue |
| Qdrant (`rmi-qdrant`) | Vector embeddings |
| Neo4j (`rmi-neo4j`) | Wallet graph / labels (169K+ labels) |
| ClickHouse (`rmi-clickhouse`) | Analytics, 39M address labels |
| DuckDB | Parquet querying, sub-1GB analytics |

## Current issues
- Several large files exceed 500-line limit (x402_enforcement.py, x402_tools.py, databus files)
- SQLite used in 5 files for local state
- Mixed structured/unstructured logging patterns remain
- Some routes in `app/routers/` are kept for utility code (x402_enforcement, etc.) — routes are side effects, not hit
