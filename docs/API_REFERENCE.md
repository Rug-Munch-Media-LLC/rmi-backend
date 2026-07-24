# RMI Backend API Reference

> **Rug Munch Intelligence** — institutional-grade crypto intelligence API.
> 13+ chains, 96 data providers, 8 MCP tools, x402 pay-per-call tier, sovereign-first FOSS.
>
> **Base URL:** `https://api.rugmunch.io` (production) · `http://127.0.0.1:8000` (local)
> **OpenAPI:** `GET /openapi.json` | **Swagger UI:** `GET /docs` | **ReDoc:** `GET /redoc`

---

## Table of Contents

1. [Authentication & Authorization](#1-authentication--authorization)
2. [Scanner (v2)](#2-scanner-v2)
3. [Market Intelligence](#3-market-intelligence)
4. [Wallet Intelligence](#4-wallet-intelligence)
5. [x402 DataBus Tools (Pay-per-call)](#5-x402-databus-tools-pay-per-call)
6. [x402 Growth & Billing](#6-x402-growth--billing)
7. [Security Intelligence](#7-security-intelligence)
8. [Token Intelligence](#8-token-intelligence)
9. [Catalog Service](#9-catalog-service)
10. [Bulletin Board](#10-bulletin-board)
11. [Intelligence Feed](#11-intelligence-feed)
12. [Community (Scam School)](#12-community-scam-school)
13. [Health & Observability](#13-health--observability)
14. [Developer Portal](#14-developer-portal)
15. [MCP Tools](#15-mcp-tools)
16. [Rate Limits & Tiers](#16-rate-limits--tiers)
17. [Error Handling](#17-error-handling)
18. [Appendix: x402 Payment Protocol](#18-appendix-x402-payment-protocol)

---

## 1. Authentication & Authorization

### Auth Methods

| Method | Header | Used By |
|--------|--------|---------|
| **x402 Pay-per-call** | `X-PAYMENT`, `X-WALLET`, `X-Device-Id` | DataBus tools (paid tier) |
| **Developer API Key** | `X-API-Key` | Developer portal, v1 routes |
| **Supabase JWT** | `Authorization: Bearer <jwt>` | Bulletin, scam school, user-specific routes |
| **Admin Key** | `X-Admin-Key` (env: `ADMIN_KEY`) | Bulletin moderation, admin routes |
| **Public (no auth)** | — | Health, catalog read, status page |

### Auth Endpoints

#### `GET /api/v1/auth/jwks`
Get JWKS public key for JWT verification.

- **Tags:** `auth`
- **Auth:** None (public)
- **Rate limit:** 100 req/min
- **Response:** `{ "keys": [...] }` — JSON Web Key Set

#### `GET /.well-known/x402`
x402 protocol discovery endpoint.

- **Tags:** `x402-MCP`
- **Auth:** None (public)
- **Response:** MCP server info for x402 payment protocol

---

## 2. Scanner (v2)

Comprehensive token and wallet scanning with 50+ security modules.

### `POST /api/v2/scanner/token/scan`

Comprehensive token scan across 50+ security modules. Returns safety score, risk flags, green flags, and detailed module results.

- **Tags:** `scanner-v2`
- **Auth:** API key or admin key
- **Rate limit:** 10 req/min (free), 60 req/min (paid)
- **Request body:**
  ```json
  {
    "chain": "ethereum",
    "address": "0x..."
  }
  ```
- **Response:**
  ```json
  {
    "score": 85,
    "risk_flags": ["honeypot_detected"],
    "green_flags": ["liquidity_locked", "owner_renounced"],
    "modules": {
      "honeypot": { "passed": true },
      "rugpull": { "risk": "low" }
    }
  }
  ```

### `POST /api/v2/scanner/wallet/scan`

Comprehensive wallet scan with entity clustering, risk scoring, behavioral analysis, and RAG-based threat intelligence matching.

- **Tags:** `scanner-v2`
- **Auth:** API key or admin key
- **Rate limit:** 5 req/min (free), 30 req/min (paid)
- **Request body:**
  ```json
  {
    "chain": "solana",
    "address": "0x..."
  }
  ```
- **Response:**
  ```json
  {
    "risk_score": 42,
    "entity_labels": ["known_mev_bot"],
    "clusters": [...],
    "associated_scams": [...]
  }
  ```

---

## 3. Market Intelligence

56 endpoints under `market-intel` tag providing market overview, movers, funding rates, smart money flows, and prediction market data.

### `GET /api/v1/market/overview`

Get market overview including total market cap, BTC dominance, 24h volume, and sector performance.

- **Tags:** `market-intel`
- **Auth:** None (public) / API key (premium)
- **Rate limit:** 30 req/min (free), 300 req/min (paid)
- **Query params:** `currency` (default: usd)
- **Response:**
  ```json
  {
    "total_market_cap": 2500000000000,
    "btc_dominance": 48.5,
    "volume_24h": 85000000000,
    "sectors": { "defi": { "change_24h": 2.3 } }
  }
  ```

### `GET /api/v1/market/movers`

Top gainers and losers from CoinGecko.

- **Tags:** `market-intel`
- **Auth:** None (public)
- **Rate limit:** 30 req/min
- **Query params:** `limit` (default: 10), `direction` (gainers/losers)

### `GET /api/v1/market/funding`

Funding rates from Binance for perpetual futures.

- **Tags:** `market-intel`
- **Auth:** None (public)
- **Rate limit:** 30 req/min

### `GET /api/v1/market/smart-money`

Smart money flows by sector — tracks institutional and whale capital movements.

- **Tags:** `market-intel`
- **Auth:** API key required
- **Rate limit:** 10 req/min (free tier)

### Market Intel Premium Routes

| Endpoint | Description | Auth | Cost |
|----------|-------------|------|------|
| `GET /market/hyperliquid/*` | Hyperliquid order book & funding | API key | Premium |
| `GET /market/whales` | Whale transaction monitoring | API key | Premium |
| `GET /market/flow` | Capital flow analysis | API key | Premium |

---

## 4. Wallet Intelligence

Wallet clustering, entity resolution, fund flow tracing, and cross-chain wallet profiling.

### `GET /api/v1/wallet/{address}`

Resolve wallet address to known entity, tags, and risk score.

- **Tags:** `wallet`
- **Auth:** None (public basic) / API key (detailed)
- **Rate limit:** 30 req/min
- **Response:**
  ```json
  {
    "address": "0x...",
    "entity": "binance_hot_wallet",
    "risk_score": 5,
    "tags": ["exchange", "deposit"],
    "first_seen": "2024-01-15",
    "tx_count": 15234
  }
  ```

### `GET /api/v1/wallet/{address}/labels`

Get entity labels for a wallet address — exchange tags, mixer tags, scam tags, etc.

- **Tags:** `wallet`
- **Auth:** API key
- **Rate limit:** 30 req/min

### `GET /api/v1/wallet/{address}/history`

Get transaction history for a wallet.

- **Tags:** `wallet`
- **Auth:** API key
- **Rate limit:** 20 req/min

### `POST /api/v1/wallet-clusters/detect`

Detect wallet clusters via on-chain heuristics (funding patterns, shared deployers, co-occurrence).

- **Tags:** `wallet-clustering`
- **Auth:** API key
- **Rate limit:** 10 req/min

### `POST /api/v1/wallet-clusters/funding-path`

Trace funding path from a wallet back to its origin (CEX, mining, airdrop).

- **Tags:** `wallet-clustering`
- **Auth:** API key (premium)
- **Rate limit:** 5 req/min

---

## 5. x402 DataBus Tools (Pay-per-call)

39 pay-per-call endpoints for on-chain data, wallet analytics, market data, and security scanning. Each call costs $0.01–$0.10 USDC via the x402 payment protocol.

**Auth:** x402 payment header (X-PAYMENT) or trial via X-WALLET/X-Device-Id

**Trial policy:**
- Connected wallets: full trial allotment (refreshes daily)
- Device fingerprints: 1 trial per tool
- IP-only: 1 trial total

### Market Data Tools

| Endpoint | Description | Price |
|----------|-------------|-------|
| `POST /api/v1/x402-databus/trending` | Trending tokens feed | $0.01 |
| `POST /api/v1/x402-databus/market-movers` | Top market movers | $0.01 |
| `POST /api/v1/x402-databus/token-price` | Real-time token price | $0.01 |
| `POST /api/v1/x402-databus/token-detail` | Full token metadata | $0.01 |
| `POST /api/v1/x402-databus/tvl` | DeFi TVL tracker | $0.01 |
| `POST /api/v1/x402-databus/news` | Crypto news feed | $0.01 |
| `POST /api/v1/x402-databus/dex-data` | DEX liquidity data | $0.01 |
| `POST /api/v1/x402-databus/defi-protocols` | DeFi protocol tracker | $0.01 |
| `POST /api/v1/x402-databus/prediction-markets` | Prediction market data | $0.02 |
| `POST /api/v1/x402-databus/social-feed` | Social sentiment feed | $0.01 |

### Wallet Tools

| Endpoint | Description | Price |
|----------|-------------|-------|
| `POST /api/v1/x402-databus/wallet-balance` | Wallet balance check | $0.01 |
| `POST /api/v1/x402-databus/wallet-labels` | Wallet entity lookup | $0.02 |
| `POST /api/v1/x402-databus/wallet-profile` | Full wallet profile | $0.03 |
| `POST /api/v1/x402-databus/wallet-tokens` | Wallet token holdings | $0.05 |
| `POST /api/v1/x402-databus/wallet-pnl` | Wallet PnL tracker | $0.05 |
| `POST /api/v1/x402-databus/portfolio` | Multi-wallet portfolio | $0.05 |
| `POST /api/v1/x402-databus/smart-money` | Smart money tracker | $0.05 |
| `POST /api/v1/x402-databus/funding-source` | Funding source tracer | $0.08 |
| `POST /api/v1/x402-databus/bundle-detect` | Bot activity detector | $0.08 |
| `POST /api/v1/x402-databus/cross-chain` | Cross-chain wallet lookup | $0.03 |
| `POST /api/v1/x402-databus/wallet-cluster` | Wallet cluster analysis | $0.05 |
| `POST /api/v1/x402-databus/socialfi-resolve` | Social → wallet resolve | $0.02 |

### Premium Data Tools

| Endpoint | Description | Price |
|----------|-------------|-------|
| `POST /api/v1/x402-databus/arkham-portfolio` | Arkham-style portfolio | $0.08 |
| `POST /api/v1/x402-databus/arkham-transfers` | Arkham-style transfer graph | $0.08 |
| `POST /api/v1/x402-databus/nansen-labels` | Nansen-style labels | $0.05 |
| `POST /api/v1/x402-databus/rugmap` | Holder RugMap visualization | $0.02 |
| `POST /api/v1/x402-databus/risk-scan` | RugShield quick scan | $0.02 |
| `POST /api/v1/x402-databus/threat-check` | Threat intel check | $0.02 |
| `POST /api/v1/x402-databus/contract-scan` | Deep contract auditor | $0.08 |
| `POST /api/v1/x402-databus/sentinel-deep` | SENTINEL deep scan | $0.10 |
| `POST /api/v1/x402-databus/entity-intel` | Entity intelligence | $0.05 |
| `POST /api/v1/x402-databus/rag-search` | RAG search on scam intel | $0.03 |

**Example Request:**
```json
POST /api/v1/x402-databus/wallet-balance
Headers:
  X-Wallet: 0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18
  X-Device-Id: my-app-v1

{
  "chain": "ethereum",
  "address": "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"
}
```

**Example Response:**
```json
{
  "balance": 1.234,
  "decimals": 18,
  "symbol": "ETH",
  "usd_value": 4123.45,
  "trial": true,
  "remaining": 4
}
```

### Free Discovery Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/x402-databus/catalog` | List all available DataBus tools with pricing |
| `GET /api/v1/x402-databus/access-matrix` | Access control matrix by tier |
| `GET /api/v1/x402-databus/trials/{identifier}` | Check remaining trials for an identifier |

---

## 6. x402 Growth & Billing

Webhook management, affiliate system, invoice/payment endpoints, and usage tracking.

### Webhooks

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/x402/webhooks/register` | Register a new webhook URL | API key |
| `GET` | `/api/v1/x402/webhooks` | List registered webhooks | API key |
| `DELETE` | `/api/v1/x402/webhooks/{hook_id}` | Delete a webhook | API key |
| `GET` | `/api/v1/x402/webhooks/events` | List webhook events | API key |

**Example:**
```json
POST /api/v1/x402/webhooks/register
{
  "url": "https://myapp.com/webhook",
  "events": ["scan.complete", "alert.triggered"],
  "secret": "whsec_..."
}
```

### Payments & Invoices

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/x402/pay/{invoice_id}` | Pay an invoice |
| `GET` | `/api/v1/x402/invoice/{invoice_id}` | Get invoice details |
| `GET` | `/api/v1/x402/usage` | Get usage statistics |

### Affiliates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/x402/affiliates/register` | Register as an affiliate |
| `GET` | `/api/v1/x402/affiliates/{code}` | Lookup affiliate by code |
| `GET` | `/api/v1/x402/affiliates` | List all affiliates |

### Stripe Integration

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/stripe/checkout` | Create Stripe checkout session | API key |
| `POST` | `/api/v1/stripe/webhook` | Stripe webhook receiver | Stripe signature |
| `GET` | `/api/v1/stripe/packages` | List available credit packages | None |

---

## 7. Security Intelligence

31 endpoints for contract analysis, wallet risk scoring, threat detection, portfolio monitoring, and fund flow analysis.

### Contract Analysis

#### `GET /api/v1/security/contract/{address}`

Get full contract security analysis — Mythril + Slither results, vulnerability matches, risk scoring.

- **Tags:** `security`
- **Auth:** API key
- **Rate limit:** 10 req/min
- **Response:**
  ```json
  {
    "address": "0x...",
    "verified": true,
    "vulnerabilities": [
      {
        "type": "reentrancy",
        "severity": "high",
        "location": "withdraw()@L42"
      }
    ],
    "score": 72,
    "risk_level": "medium"
  }
  ```

#### `POST /api/v1/security/scan/batch`

Batch contract scan — up to 20 contracts per request.

- **Tags:** `security`
- **Auth:** API key (premium)
- **Rate limit:** 5 req/min

### Wallet Security

| Method | Endpoint | Description | Rate Limit |
|--------|----------|-------------|------------|
| `GET` | `/api/v1/security/wallet/{address}` | Full wallet security check | 10/min |
| `POST` | `/api/v1/security/monitor/watch` | Add wallet to watchlist | 20/min |
| `GET` | `/api/v1/security/monitor/watches` | List watched wallets | 30/min |
| `GET` | `/api/v1/security/portfolio/{wallet}` | Single wallet portfolio | 10/min |
| `GET` | `/api/v1/security/portfolio/history/{wallet}` | Historical net worth | 5/min |

### Threat Intelligence

| Method | Endpoint | Description | Price |
|--------|----------|-------------|-------|
| `POST` | `/api/v1/security/threat/check` | Batch threat intel lookup | $0.02 |
| `POST` | `/api/v1/security/flows/analyze` | Fund flow analysis | $0.08 |
| `POST` | `/api/v1/security/entity/batch` | Batch entity label lookup | $0.02 |
| `GET` | `/api/v1/security/entity/search` | Search entity database | Free |

### Security Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/security/dashboard/stats` | Security dashboard statistics |
| `GET` | `/api/v1/security/dashboard/alerts` | Query security alerts |
| `POST` | `/api/v1/security/dashboard/acknowledge` | Acknowledge an alert |
| `GET` | `/api/v1/security/dashboard/score` | Get rolling security score |
| `GET` | `/api/v1/security/health` | Full subsystem health check |

---

## 8. Token Intelligence

### v1 Token Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/tokens/{address}` | Basic token metadata (name, symbol, decimals, supply) | None |
| `GET` | `/api/v1/tokens/{address}/risk` | Token risk assessment | API key |
| `GET` | `/token/{address}` | Legacy token metadata (root route) | None |
| `GET` | `/token/{address}/risk` | Legacy token risk | API key |
| `GET` | `/token/{address}/holders` | Token holder distribution | API key |

### v2 Token Intelligence

#### `GET /api/v2/tokens/{address}/intelligence`

Advanced token intelligence with ML-based risk analysis, social sentiment correlation, and holder behavior patterns.

- **Tags:** `token-intelligence-v2`
- **Auth:** API key (premium)
- **Rate limit:** 5 req/min

### Token Risk Parameters

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `chain` | string | `ethereum` | Blockchain to query (ethereum, solana, bsc, polygon, arbitrum, optimism, base, avalanche) |
| `include_holders` | bool | `false` | Include holder distribution in response |
| `include_liquidity` | bool | `true` | Include liquidity pool analysis |

---

## 9. Catalog Service

The catalog service provides unified access to the token, wallet, and entity database with cross-store linking.

### Token Catalog

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/catalog/tokens/{chain}/{address}` | Get token by chain+address |
| `GET` | `/api/v1/catalog/tokens/{chain}/{address}/risk` | Real-time risk score (Redis+Postgres+Neo4j) |
| `POST` | `/api/v1/catalog/tokens/risky-by-deployer` | Find tokens from rug-history deployers |
| `POST` | `/api/v1/catalog/tokens/{chain}/{address}/attach-rag` | Link RAG embedding to token |

### Wallet Catalog

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/catalog/wallets/{chain}/{address}` | Look up wallet by chain+address. Returns full wallet model with provenance, entity labels, and transaction history |

### RAG Bridge

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/catalog/rag/search` | Search RAG system for scam intelligence |
| `POST` | `/api/v1/catalog/rag/ingest` | Ingest content into RAG |

### Health & Introspection

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/catalog/stats` | Store reachability + entity counts |
| `GET` | `/api/v1/catalog/probe` | Probe store connectivity |

---

## 10. Bulletin Board

Community bulletin board for announcements, posts, comments, and user reputation.

### Public Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/bulletin-board/posts` | List published posts | None |
| `GET` | `/api/v1/bulletin-board/posts/{slug}` | Get single post by slug | None |
| `GET` | `/api/v1/bulletin-board/categories` | List categories with counts | None |
| `GET` | `/api/v1/bulletin-board/pinned` | Get pinned posts | None |
| `GET` | `/api/v1/bulletin-board/badges/{user_id}` | Get user badges | None |
| `GET` | `/api/v1/bulletin-board/leaderboard` | Top users by reputation | None |

### Admin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/bulletin-board/admin/bulletin/posts/{post_id}` | Get full post details |
| `POST` | `/api/v1/bulletin-board/admin/bulletin/posts` | Create new post |
| `PUT` | `/api/v1/bulletin-board/admin/bulletin/posts/{post_id}` | Update post |
| `DELETE` | `/api/v1/bulletin-board/admin/bulletin/posts/{post_id}` | Archive post |
| `POST` | `/api/v1/bulletin-board/admin/bulletin/posts/{post_id}/publish` | Publish immediately |
| `POST` | `/api/v1/bulletin-board/admin/bulletin/posts/{post_id}/unpublish` | Return to draft |
| `POST` | `/api/v1/bulletin-board/admin/bulletin/posts/{post_id}/pin` | Toggle pin status |
| `GET` | `/api/v1/bulletin-board/admin/bulletin/stats` | Bulletin statistics |

### Bulletin (v1)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/bulletin/latest` | Latest posts | None |
| `POST` | `/api/v1/bulletin/posts` | Create post | Supabase JWT |
| `POST` | `/api/v1/bulletin/posts/{post_id}/vote` | Upvote/downvote | Supabase JWT |
| `POST` | `/api/v1/bulletin/posts/{post_id}/comments` | Add comment | Supabase JWT |
| `GET` | `/api/v1/bulletin/posts/{post_id}/comments` | Get comments | None |
| `POST` | `/api/v1/bulletin/bots/register` | Register bot | API key |
| `POST` | `/api/v1/bulletin/bots/{bot_id}/post` | Bot creates post | Bot auth |
| `POST` | `/api/v1/bulletin/reports` | Report a post | Supabase JWT |
| `POST` | `/api/v1/bulletin/moderate` | Moderation action | Admin key |

---

## 11. Intelligence Feed

Real-time market intelligence, security alerts, whale tracking, and marketing graphics generation.

### Market Intelligence

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/intelligence/market/trending` | Trending tokens across chains | None |
| `GET` | `/api/v1/intelligence/market/whales` | Recent whale movements | API key |
| `GET` | `/api/v1/intelligence/market/volume-spikes` | Volume spike alerts | API key |
| `GET` | `/api/v1/intelligence/premium/smart-money` | Smart money activities | Premium |
| `GET` | `/api/v1/intelligence/premium/exchange-flows` | Exchange inflow/outflow | Premium |

### Security Intelligence

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/intelligence/security/scams` | Scam alerts | API key |
| `GET` | `/api/v1/intelligence/security/rugpulls` | Rugpull alerts | API key |
| `POST` | `/api/v1/intelligence/market-update` | Ingest market update | Internal |
| `POST` | `/api/v1/intelligence/security-update` | Ingest security update | Internal |

### Personal Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/intelligence/me/alerts` | Get my personalized alerts |
| `POST` | `/api/v1/intelligence/me/alerts/{alert_id}/dismiss` | Dismiss an alert |

### Marketing Graphics

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/intelligence/cards/templates` | List card templates |
| `POST` | `/api/v1/intelligence/marketing/generate/stats` | Generate stats graphic |
| `POST` | `/api/v1/intelligence/marketing/generate/pricing` | Generate pricing graphic |

---

## 12. Community (Scam School)

Educational platform teaching users to identify and avoid crypto scams. 44 endpoints for courses, modules, exams, achievements, and leaderboards.

### Courses

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/scam-school/courses` | List all courses |
| `GET` | `/api/v1/scam-school/courses/{course_id}` | Get course details |
| `GET` | `/api/v1/scam-school/courses/{course_id}/modules/{module_id}` | Get module content |

### Progress & Achievements

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/scam-school/progress` | Get current user's full progress |
| `GET` | `/api/v1/scam-school/badges` | List all available badges |
| `GET` | `/api/v1/scam-school/achievements` | List all achievements |
| `GET` | `/api/v1/scam-school/levels` | List level progression |
| `POST` | `/api/v1/scam-school/exams/{course_id}/{exam_id}` | Submit exam and get results |
| `GET` | `/api/v1/scam-school/leaderboard` | Global leaderboard by XP |

---

## 13. Health & Observability

### Kubernetes-style Health Checks

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/health` | Full health check (all stores) | None |
| `GET` | `/live` | Liveness probe (process alive) | None |
| `GET` | `/ready` | Readiness probe (critical deps) | None |
| `GET` | `/metrics` | Prometheus metrics | Internal |

**Health response:**
```json
{
  "status": "healthy",
  "version": "2026.06.21",
  "uptime_seconds": 3600.0,
  "stores": {
    "redis": { "healthy": true },
    "postgres": { "healthy": true },
    "neo4j": { "healthy": true },
    "qdrant": { "healthy": true, "details": { "version": "1.9.0" } },
    "clickhouse": { "healthy": true, "details": { "databases": ["rmi"] } },
    "minio": { "healthy": true }
  }
}
```

### v1 Health Redirects

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/health/live` | 307 redirect to `/live` |
| `GET` | `/api/v1/health/ready` | 307 redirect to `/ready` |
| `GET` | `/api/v1/health` | 307 redirect to `/health` |

### Unified Health Dashboard

#### `GET /api/v1/health/unified`

Aggregated health dashboard combining service health, provider status, degradation info, and config validation. Cached for 15 seconds.

```json
{
  "status": "healthy",
  "timestamp": "2026-07-24T12:00:00Z",
  "services": {
    "redis": { "healthy": true, "latency_ms": 2 },
    "postgres": { "healthy": true },
    "neo4j": { "healthy": false, "error": "Connection refused" }
  },
  "providers": {
    "coingecko": "healthy",
    "dexscreener": "degraded"
  },
  "degradation": {
    "degraded_stores": ["neo4j"],
    "lru_stats": { "size": 42, "hits": 1500, "misses": 200 }
  }
}
```

### Application Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/security/health` | Security subsystem health |
| `GET` | `/api/v1/intelligence/health` | Intelligence service health |
| `GET` | `/api/v1/bulletin/health` | Bulletin system health |

### Tracing

#### `GET /api/v1/traces/recent`

View recent OpenTelemetry traces. When OTEL is enabled, traces are exported to Grafana Tempo.

- **Tags:** `observability`
- **Auth:** Internal
- **Note:** Returns empty list when tracing is disabled

---

## 14. Developer Portal

API key management, usage tracking, and tier management for developers.

### API Keys

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/developer/keys` | Create a new API key | Supabase JWT |
| `GET` | `/api/v1/developer/keys` | List your API keys | Supabase JWT |
| `DELETE` | `/api/v1/developer/keys/{key}` | Revoke an API key | Supabase JWT |
| `GET` | `/api/v1/developer/usage` | Get usage stats | Supabase JWT |

### Developer Tiers

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/developer/register` | Register as developer |
| `POST` | `/api/v1/developer/verify` | Verify API key |
| `POST` | `/api/v1/developer/usage` | Get detailed usage for a key |
| `GET` | `/api/v1/developer/tiers` | List available tiers |
| `POST` | `/api/v1/developer/wallet/verify` | Verify wallet ownership |
| `POST` | `/api/v1/developer/wallet/get_challenge` | Get wallet challenge |
| `GET` | `/api/v1/developer/wallet/status/{chain}/{address}` | Check wallet status |

### Tier Pricing

| Tier | Price | Rate Limit | Features |
|------|-------|------------|----------|
| Free | $0 | 30 req/min | Basic scanner, public data |
| Developer | $29/mo | 300 req/min | Full scanner, market intel |
| Premium | $99/mo | 3000 req/min | All endpoints, priority support |
| Enterprise | Custom | Custom | SLA, dedicated infra |

---

## 15. MCP Tools

Model Context Protocol (MCP) endpoints for AI agent integration.

### Standard MCP

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/mcp` | JSON-RPC MCP handler |
| `GET` | `/mcp/tools` | List MCP tools |
| `GET` | `/mcp/info` | Server metadata |

### x402 MCP

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/mcp/x402` | x402 payment-gated MCP handler |
| `GET` | `/.well-known/x402` | x402 protocol discovery |

### MCP Tool Catalog

| Tool | Description | Pricing |
|------|-------------|---------|
| `scan_token` | Token security scan | Free (5/day) |
| `scan_wallet` | Wallet security scan | Free (5/day) |
| `cross_chain_trace` | Multi-chain fund trace | $0.12/call |
| `wallet_intel` | Wallet intelligence | $0.05/call |
| `market_data` | Market data query | Free |
| `threat_check` | Threat intelligence | $0.02/call |
| `rugmap` | Holder visualization | $0.02/call |
| `smart_money` | Smart money tracking | $0.05/call |

---

## 16. Rate Limits & Tiers

### Rate Limit Tiers

| Tier | Global Rate | Scanner | Market Intel | x402 Tools | Burst |
|------|-------------|---------|-------------|------------|-------|
| **Public** | 30 req/min | 10/min | 30/min | — | 5 req |
| **Developer** | 300 req/min | 60/min | 300/min | 60/min | 20 req |
| **Premium** | 3000 req/min | 300/min | 1000/min | 300/min | 100 req |
| **Enterprise** | Custom | Custom | Custom | Custom | Custom |

### Rate Limit Headers

All responses include:
- `X-RateLimit-Limit` — requests allowed per window
- `X-RateLimit-Remaining` — requests remaining in window
- `X-RateLimit-Reset` — window reset timestamp (Unix)
- `Retry-After` — seconds to wait (only on 429)

### 429 Response

```json
{
  "detail": "Rate limit exceeded. Try again in 30 seconds.",
  "retry_after": 30
}
```

### x402 Tool Pricing

| Price Tier | Tools | Trial Allotment |
|------------|-------|-----------------|
| $0.01 | basic market data, wallet balance | 5/day |
| $0.02 | labels, risk scan, threat check | 3/day |
| $0.03–$0.05 | wallet profile, smart money, PnL | 2/day |
| $0.08 | funding source, contract scan, bundle detect | 1/day |
| $0.10 | SENTINEL deep scan | 1/day |

---

## 17. Error Handling

### HTTP Status Codes

| Code | Meaning | When |
|------|---------|------|
| `200` | Success | Request completed |
| `201` | Created | Resource created |
| `307` | Redirect | Health check redirects |
| `400` | Bad Request | Invalid parameters |
| `401` | Unauthorized | Missing/invalid API key |
| `402` | Payment Required | x402 payment needed |
| `403` | Forbidden | Insufficient tier |
| `404` | Not Found | Resource not found |
| `429` | Too Many Requests | Rate limit exceeded |
| `500` | Internal Error | Server-side failure |

### Error Response Format

All errors follow RFC 7807 (Problem Details):

```json
{
  "type": "https://api.rugmunch.io/errors/payment_required",
  "title": "Payment Required",
  "status": 402,
  "detail": "This endpoint requires x402 payment.",
  "instance": "/api/v1/x402-databus/sentinel-deep",
  "context": {
    "tool_id": "sentinel-deep",
    "price_usd": 0.10,
    "trial_free": 1
  }
}
```

### x402 Payment Error

When a paid endpoint is called without payment or exhausted trials:

```json
{
  "detail": "payment_required",
  "context": {
    "tool_id": "sentinel-deep",
    "price_usd": 0.10,
    "payment_required": {
      "x402_version": 1,
      "max_amount": "0.1000",
      "payment_type": "USDC",
      "networks": ["base", "solana", "ethereum", "bsc"],
      "connect_wallet_for_trials": true
    }
  }
}
```

### Idempotency

All x402 paid endpoints support idempotent retry via `X-Idempotency-Key` header. Responses are cached for 24 hours to prevent double-charging.

---

## 18. Appendix: x402 Payment Protocol

### How It Works

1. **Client connects wallet** → sends `X-Wallet` header with address
2. **Free trial** → first N calls per tool per day are free (varies by tool)
3. **Payment required** → 402 response includes payment metadata
4. **Client pays** → sends signed USDC transaction to platform address
5. **Payment header** → `X-PAYMENT: <signed_payment>` unlocks the call

### Supported Networks

| Network | USDC Address |
|---------|-------------|
| Base | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Solana | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` |
| Ethereum | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` |
| BSC | `0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d` |
| Arbitrum | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` |
| Optimism | `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85` |
| Polygon | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` |

---

## Quick Reference: Top 20 Most-Used Endpoints

| # | Method | Path | Auth | Cost | Rate Limit |
|---|--------|------|------|------|------------|
| 1 | `POST` | `/api/v2/scanner/token/scan` | API key | Free (5/day) | 10/min |
| 2 | `POST` | `/api/v2/scanner/wallet/scan` | API key | Free (5/day) | 5/min |
| 3 | `GET` | `/api/v1/market/overview` | None | Free | 30/min |
| 4 | `GET` | `/api/v1/market/movers` | None | Free | 30/min |
| 5 | `GET` | `/api/v1/wallet/{address}` | None | Free | 30/min |
| 6 | `GET` | `/api/v1/tokens/{address}` | None | Free | 30/min |
| 7 | `POST` | `/api/v1/x402-databus/wallet-balance` | x402 | $0.01 | 60/min |
| 8 | `POST` | `/api/v1/x402-databus/trending` | x402 | $0.01 | 60/min |
| 9 | `GET` | `/api/v1/security/contract/{address}` | API key | Free | 10/min |
| 10 | `POST` | `/api/v1/security/threat/check` | API key | $0.02 | 20/min |
| 11 | `GET` | `/api/v1/intelligence/market/trending` | None | Free | 30/min |
| 12 | `GET` | `/api/v1/intelligence/security/rugpulls` | API key | Free | 20/min |
| 13 | `GET` | `/api/v1/developer/usage` | JWT | Free | 30/min |
| 14 | `POST` | `/api/v1/stripe/checkout` | API key | N/A | 10/min |
| 15 | `GET` | `/api/v1/bulletin-board/posts` | None | Free | 30/min |
| 16 | `POST` | `/api/v1/bulletin/posts` | JWT | Free | 20/min |
| 17 | `GET` | `/health` | None | Free | 60/min |
| 18 | `GET` | `/api/v1/catalog/tokens/{chain}/{address}` | None | Free | 30/min |
| 19 | `GET` | `/api/v1/catalog/wallets/{chain}/{address}` | None | Free | 30/min |
| 20 | `POST` | `/api/v1/x402-databus/sentinel-deep` | x402 | $0.10 | 30/min |

---

> **Last updated:** 2026-07-24
> **API version:** 2026.06.21
> **OpenAPI spec:** `GET /openapi.json`
> **Support:** admin@rugmunch.io
