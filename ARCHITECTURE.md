# RMI Backend — Architecture

**Status:** canonical  
**Owner:** Rug Munch Media LLC Engineering  
**Last updated:** 2026-07-25  

---

## Overview

FastAPI monolith (strangler fig pattern) serving crypto risk intelligence via HTTP API, WebSocket, Telegram bot, and MCP protocol. Six-database architecture with unified data access through DataBus.

**Scale:** Large route surface · Extensive MCP tools · Multi-provider DataBus · Large wallet label corpus · Multi-module scanner engine

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        INFRASTRUCTURE LAYER                      │
│  ┌──────────┐  ┌──────────┐  ┌──────┐  ┌──────┐  ┌─────────┐  │
│  │ Postgres │  │ClickHouse│  │ Redis│  │ Neo4j│  │  Qdrant │  │
│  └────┬─────┘  └────┬─────┘  └──┬───┘  └──┬───┘  └────┬────┘  │
└───────┼─────────────┼───────────┼─────────┼───────────┼────────┘
        │             │           │         │           │         │
┌───────┼─────────────┼───────────┼─────────┼───────────┼────────┐
│       ▼             ▼           ▼         ▼           ▼        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                     APP LAYER                            │   │
│  │  ┌──────────────────────────────────────────────────┐   │   │
│  │  │            rmi-backend (FastAPI)                  │   │   │
│  │  │  Port 8000 · read-only rootfs           │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  │         │          │           │                         │   │
│  │  ┌──────┴──────┐ ┌─┴────────┐ ┌┴──────────┐             │   │
│  │  │ HTTP API    │ │Telegram   │ │ MCP       │             │   │
│  │  │ routes      │ │bot        │ │ tools     │             │   │
│  │  └─────────────┘ └──────────┘ └───────────┘             │   │
└───────┬──────────────────────────────────────────────────────┘
        │
┌───────┼──────────────────────────────────────────────────────┐
│       ▼              GATEWAY LAYER                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐             │
│  │  nginx   │  │  x402 Gateway│  │  TLS       │             │
│  │  routing │  │  paid tools  │  │  termination│             │
│  └──────────┘  └──────────────┘  └────────────┘             │
└──────────────────────────────────────────────────────────────┘
```

## Service Stack

| Layer | Components |
|-------|-----------|
| Core Backend | FastAPI application |
| Databases | Postgres, ClickHouse, Redis, Neo4j, Qdrant, DuckDB |
| Infrastructure | nginx, Prometheus, Grafana, message queue |
| External APIs | 30+ blockchain and data provider APIs |

## Data Flow

```
User/Agent Request
    │
    ▼
[CDN]  ← TLS termination
    │
    ▼
[Gateway] ← Rate limiting · Path routing · Security headers
    │                         ┌──────────────────────┐
    ├── /api/v1/x402/*  ─────▶│ x402 Gateway         │
    │                         │ (paid tools)         │
    ├── /.well-known/x402 ───▶│                      │
    │                         └──────────────────────┘
    │                         ┌──────────────────────┐
    └── /* ──────────────────▶│ Backend Application  │
                              │ (all other routes)   │
                              └──────────────────────┘
                                    │
                    ┌───────────────┼───────────────────┐
                    ▼               ▼                   ▼
            [DataBus Layer]   [Auth Layer]        [Scanner Engine]
                    │               │                   │
                    ▼               ▼                   ▼
            [External APIs]   [JWT/RS256]         [Scanner Modules]
            [30+ providers]   [OAuth/Wallet]     [Risk Engine]
```

## App Factory & Mounting

```
create_app() → FastAPI instance
  ├── lifespan (app/core/lifespan.py)
  │   ├── Startup: connect DBs, init Redis, warm caches
  │   └── Shutdown: graceful drain, close connections
  ├── error handlers (app/core/errors.py)
  │   └── RFC 7807 Problem Details for all error responses
  ├── middleware (registered via app/middleware_setup.py)
  └── mount_all() (app/mount.py — router modules)
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
├── scanner/     → Token risk scanner modules
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

**Key files:** in `app/databus/`, providers in `app/databus/providers/`

## Scaling

- **Horizontal:** Stateless backend behind load balancer
- **Vertical:** Configurable CPU/memory allocation per environment
- **Database:** Connection pooling, read replicas as needed

## Observability

| System | Stack |
|--------|-------|
| Metrics | Prometheus + Grafana |
| Logs | Structured JSON logging |
| Traces | OpenTelemetry |
| Alerts | Prometheus Alertmanager |
| Status | Uptime monitoring |

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
│  6. CI/CD: Gitleaks (pre-commit), Trivy (container), Bandit/SAST│
└──────────────────────────────────────────────────────────────────┘
```

## Known Technical Debt

| Issue | Impact | Target |
|-------|--------|--------|
| Low test coverage | Risk of regressions | Improve coverage |
| `print()` calls in production code | Poor log hygiene | Replace with structured logging |
| `time.sleep()` usage | Blocking I/O | Replace with `asyncio.sleep()` |
| Large module files | Maintainability | Split by domain |
| CI soft failures | Silent regressions | Hard gates |

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
