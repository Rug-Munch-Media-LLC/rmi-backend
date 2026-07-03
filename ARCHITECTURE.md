# RMI Backend — Architecture

**Status:** canonical
**Last updated:** 2026-07-03

## Overview
FastAPI monolith serving crypto risk intelligence via HTTP, Telegram, and MCP.

## High-level flow
```
Client (Web / Telegram / AI Agent)
    ↓
nginx → rmi-backend container (:8000)
    ↓
FastAPI routers
    ↓
Services / scanners / DataBus providers
    ↓
Postgres / Redis / Qdrant / Neo4j / ClickHouse
```

## Components
| Path | Purpose |
|---|---|
| `app/routers/` | HTTP route handlers (thin) |
| `app/scanners/` | Token risk scanners |
| `app/databus/` | 30+ third-party data providers |
| `app/telegram_bot/` | Telegram bot command handlers |
| `app/mcp/` | MCP manifest and tool manager |
| `app/facilitators/` | x402 payment verification |
| `app/wallet_memory/` | Wallet label + clustering subsystem |

## Storage
| Store | Use | Container / Host |
|---|---|---|
| Postgres | Primary OLTP, Ponder indexer data | `rmi-postgres` |
| Redis | Cache, Celery queue | `rmi-redis` |
| Qdrant | Vector embeddings | `rmi-qdrant` (down) |
| Neo4j | Wallet graph / labels | `rmi-neo4j` |
| ClickHouse | Analytics, 39M address labels | `rmi-clickhouse` |

## Interfaces
| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check (currently unhealthy due to Redis) |
| `GET /docs` | Swagger/OpenAPI docs |
| `POST /scanner/scan` | Live token scan |
| `POST /mcp/...` | MCP tool endpoints |

## Current issues
- `x402_tools.py` (212 KB) and `x402_enforcement.py` (108 KB) violate the 500-line limit and should become separate services or split by domain.
- `sqlite3` is used in 5 files for local state; should migrate to Postgres/aiosqlite.
- F-string logs and `print()` statements prevent structured log aggregation.

## Deployment
```bash
ssh netcup
cd /root/backend/
docker compose pull
docker compose up -d rmi-backend
```
