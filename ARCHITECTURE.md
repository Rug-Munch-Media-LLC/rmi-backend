# RMI Backend — Architecture

**Status:** canonical  
**Owner:** Rug Munch Media LLC Engineering  
**Last updated:** 2026-07-25  

---

## Overview

FastAPI monolith (strangler fig pattern) serving crypto risk intelligence via HTTP API, WebSocket, Telegram bot, and MCP protocol. Six-database architecture with unified data access through DataBus.

**Scale:** 780+ routes · 221 MCP tools · 89 DataBus chains · 39.4M wallet labels · 21 SENTINEL scanner modules

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        INFRASTRUCTURE LAYER                      │
│  ┌──────────┐  ┌──────────┐  ┌──────┐  ┌──────┐  ┌─────────┐  │
│  │ Postgres │  │ClickHouse│  │ Redis│  │ Neo4j│  │  Qdrant │  │
│  │ rmi-pg   │  │ rmi-ch   │  │rmi-rl│  │rmi-n4j│  │ rmi-qd  │  │
│  │ 5432     │  │ 8123     │  │ 6379 │  │ 7687 │  │ 6333    │  │
│  └────┬─────┘  └────┬─────┘  └──┬───┘  └──┬───┘  └────┬────┘  │
└───────┼─────────────┼───────────┼─────────┼───────────┼────────┘
        │             │           │         │           │         │
┌───────┼─────────────┼───────────┼─────────┼───────────┼────────┐
│       ▼             ▼           ▼         ▼           ▼        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                     APP LAYER                            │   │
│  │  ┌──────────────────────────────────────────────────┐   │   │
│  │  │            rmi-backend (FastAPI)                  │   │   │
│  │  │  Port 8000 · 4GB RAM · read-only rootfs           │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  │         │          │           │                         │   │
│  │  ┌──────┴──────┐ ┌─┴────────┐ ┌┴──────────┐             │   │
│  │  │ HTTP API    │ │Telegram   │ │ MCP       │             │   │
│  │  │ 780+ routes │ │@rugmunch  │ │ 221 tools │             │   │
│  │  └─────────────┘ └──────────┘ └───────────┘             │   │
└───────┬──────────────────────────────────────────────────────┘
        │
┌───────┼──────────────────────────────────────────────────────┐
│       ▼              GATEWAY LAYER                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐             │
│  │ nginx:8080│  │rmi-mcp-x402 │  │cloudflared  │             │
│  │ route     │  │Port 8001    │  │TLS tunnel   │             │
│  │ resolver  │  │x402 paid    │  │             │             │
│  └──────────┘  └──────────────┘  └────────────┘             │
└──────────────────────────────────────────────────────────────┘
```

## Service Count (Current)

| Layer | Services | Details |
|-------|----------|---------|
| Core Backend | 2 | rmi-backend + rmi-mcp-x402 |
| Databases | 6 | Postgres, ClickHouse, Redis, Neo4j, Qdrant, DuckDB |
| Infrastructure | 8 | nginx, Prometheus, Grafana, MinIO, PgBouncer, DNS, LiteLLM, Temporal |
| External APIs | 30+ | Helius, Birdeye, CoinGecko, Etherscan, etc. |
| **Total** | **46+** | Across 20+ Docker Compose files |

## Data Flow

```
User/Agent Request
    │
    ▼
[Cloudflare/CDN]  ← TLS termination
    │
    ▼
[nginx Gateway] ← Rate limiting · Path routing · Security headers
    │                         ┌───────────────────┐
    ├── /api/v1/x402/*  ─────▶│ rmi-mcp-x402:8001 │
    │                         │ (paid tools)      │
    ├── /.well-known/x402 ───▶│                   │
    │                         └───────────────────┘
    │                         ┌───────────────────┐
    └── /* ──────────────────▶│ rmi-backend:8000  │
                              │ (all other routes)│
                              └───────────────────┘
                                    │
                    ┌───────────────┼───────────────────┐
                    ▼               ▼                   ▼
            [DataBus Layer]   [Auth Layer]        [Scanner Engine]
                    │               │                   │
                    ▼               ▼                   ▼
            [External APIs]   [JWT/RS256]         [21 Modules]
            [30+ providers]   [OAuth/Wallet]     [Fusion Engine]
```

## App Factory & Mounting

```
create_app() → FastAPI instance
  ├── lifespan (app/core/lifespan.py)
  │   ├── Startup: connect DBs, init Redis, warm caches
  │   └── Shutdown: graceful drain, close connections
  ├── error handlers (app/core/errors.py)
  │   └── RFC 7807 Problem Details for all error responses
  ├── middleware (14 registered via app/middleware_setup.py)
  └── mount_all() (app/mount.py — 53 router modules)
```

### Middleware Stack (Registration Order)

| # | Middleware | Purpose |
|---|-----------|---------|
| 1 | Request ID | X-Request-ID + X-Response-Time-Ms + structlog binding |
| 2 | CORS | Strict allowlist, production guard against `["*"]` |
| 3 | Auth | API key + JWT Bearer authentication |
| 4 | Security Headers | CSP, HSTS, XFO, XSS, Referrer-Policy, Permissions-Policy |
| 5 | Prometheus | Request metrics |
| 6 | Cost Tracking | Per-tenant cost tracking |
| 7 | Tracing | OpenTelemetry spans (gated by OTEL_ENABLED) |
| 8 | Degradation Header | X-Degraded-Stores for data source health |
| 9 | Deprecation Header | Sunset headers on legacy routes |
| 10 | SlowAPI (rate limiting) | Path-based rate limiting per category |
| 11 | Unified Rate Limiter | Token-bucket middleware |
| 12 | Slow Query Logging | Warn on >500ms endpoints |
| 13 | Content-Type Enforcement | Reject non-JSON on write endpoints |
| 14 | CSRF | Double-submit cookie pattern |

## Domain Modules

```
app/domains/
├── alerts/      → Real-time WebSocket alert broadcast
├── auth/        → JWT, OAuth, Web3 wallet, TOTP, CSRF cookies
├── billing/     → x402 payment facilitators, enforcement
├── bulletin/    → Supabase-backed bulletin system
├── databus/     → DataBus domain (strangler fig from app/databus/)
├── labels/      → Federated label sources
├── markets/     → Market data (prices, OHLCV, trending)
├── mcp/         → MCP server domain
├── news/        → News aggregation, sentiment, clusterer
├── reports/     → AI report generation
├── referral/    → Referral tracking
├── scanner/     → Token risk scanners (SENTINEL)
├── telegram/    → @rugmunchbot Telegram bot
├── threat/      → CertStream listener, threat intel
├── token/       → Token analysis, deployer
├── wallet/      → Wallet management, forensics
└── x402/        → x402 payment-gated API middleware
```

## DataBus Architecture

```
┌──────────────────────────────────────────────────────┐
│                   DataBus Layer                       │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌────────┐ │
│  │ EVM     │  │ Solana  │  │ Bitcoin  │  │ Free   │ │
│  │providers│  │providers│  │providers │  │ tier   │ │
│  └─────────┘  └─────────┘  └──────────┘  └────────┘ │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌────────┐ │
│  │ Paid    │  │ MCP     │  │ Social   │  │DuckDB  │ │
│  │ tier    │  │servers  │  │ feeds    │  │analytics│ │
│  └─────────┘  └─────────┘  └──────────┘  └────────┘ │
│                                                      │
│  Unified DataProvider ABC (app/databus/core.py)       │
│  + health monitoring + caching + access control       │
└──────────────────────────────────────────────────────┘
```

**Key files:** 30+ in `app/databus/`, providers in `app/databus/providers/`

## Deployment Topology

| Host | CPU | RAM | Role |
|------|-----|-----|------|
| **Talos** (netcup) | 12c | 31GB | Primary — all services |
| **Hydra** (contabo) | 8c | 23GB | Standby, backup, CI |
| **Cinnabox** (local) | 8c | 16GB | Development, testing |

## Scaling

- **Horizontal:** Stateless backend behind nginx load balancer
- **Vertical:** 2 CPU / 4GB RAM reserved for rmi-backend container
- **Burst:** Hydra can take over if Talos fails
- **Database:** Connection pooling via PgBouncer (Postgres), read replicas planned

## Observability

| System | Stack | Endpoint |
|--------|-------|----------|
| Metrics | Prometheus + Grafana | `/metrics`, `grafana.talos.rugmunch.io` |
| Logs | structlog + Loki | JSON structured |
| Traces | OpenTelemetry + Langfuse | `langfuse.talos.rugmunch.io` (10% sampling) |
| Alerts | Prometheus → Alertmanager → PagerDuty / n8n |
| Status | Uptime Kuma | `status.talos.rugmunch.io` |

## Security Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     SECURITY LAYERS                              │
├──────────────────────────────────────────────────────────────────┤
│  1. Network: Cloudflare WAF + nginx rate limiting (100r/s per IP)│
│  2. TLS: Cloudflare edge + Let's Encrypt (automated rotation)    │
│  3. Gateway: Request filtering, path-based routing               │
│  4. Application:                                                  │
│     ├── Auth: JWT (RS256/HS256) + API Key + x402 payment        │
│     ├── Rate Limit: 3 layers (nginx, middleware, app-level)     │
│     ├── Security Headers: CSP, HSTS, XFO, XSS, Referrer-Policy  │
│     ├── CSRF: Double-submit cookie pattern                       │
│     ├── CORS: Strict allowlist (production guard)               │
│     ├── Content-Type: Enforce JSON on write endpoints            │
│     └── Payload Limit: 1MB maximum                               │
│  5. Container: read_only, cap_drop ALL, no-new-privileges        │
│  6. Secrets: gopass → Infisical (migrating)                     │
│  7. CI/CD: Gitleaks (pre-commit), Trivy (container), Bandit/SAST│
└──────────────────────────────────────────────────────────────────┘
```

## Known Technical Debt

| Issue | Impact | Target |
|-------|--------|--------|
| 8.61% test coverage | Risk of regressions | >60% by Q4 2026 |
| 399 `print()` calls | Poor log hygiene | Eliminate by Q4 2026 |
| 17 `time.sleep()` | Blocking I/O | Replace with `asyncio.sleep()` |
| `x402_tools.py` (212 KB) | God file | Split by domain |
| CI `continue-on-error` | Silent failures | Hard gates by Q4 2026 |

## Architecture Decision Records

`docs/adr/` contains 6 ADRs covering:
- ADR-001: DataBus as Single Data Layer (accepted)
- ADR-002: Monolith over Microservices (accepted)
- ADR-003: Redis for Hot Cache, Postgres for Cold (accepted)
- ADR-004: Multi-Source Data Fusion for Risk Scoring (accepted)
- ADR-005: x402 Payment-Gated API Access (accepted)
- ADR-006: CI Quality Gates (accepted)

---

*For questions: admin@rugmunch.io*
