# RMI Backend — API Reference

> **Base URL:** `https://api.rugmunch.io` (production)  
> **Base Path:** `/api/v1` (current version)  
> **Spec:** `/openapi.json` | `/docs` (Swagger) | `/redoc` (ReDoc)  
> **Last updated:** 2026-07-23

---

## 1. Authentication

### Methods

| Method | Header / Mechanism | Used For |
|--------|-------------------|----------|
| **JWT Bearer** | `Authorization: Bearer <token>` | User-facing API (login required) |
| **API Key** | `X-API-Key: <key>` | Internal service-to-service |
| **x402 Proof** | `X-Payment-Proof: <tx_hash>` | Premium pay-per-use endpoints |
| **Cookie Session** | `csrf_token` cookie + `X-CSRF-Token` header | Web UI (double-submit CSRF) |

### Rate Limits

| Tier | Limit | Applies To |
|------|-------|-----------|
| **Anonymous** | 10 req/min per IP | Public endpoints only |
| **Free Tier** | 10 req/min per API key | Standard endpoints |
| **Premium (x402)** | 100 req/min per key | All endpoints |
| **Auth** | 5 req/min per IP | `/api/v1/auth/login`, `/register` |
| **Enterprise** | Custom | All endpoints |

Rate limit headers on every response: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

### Error Codes

All errors use [RFC 7807](https://tools.ietf.org/html/rfc7807) Problem Details format.

| Status | Code | Meaning |
|--------|------|---------|
| 400 | `validation.request_invalid` | Request validation failed |
| 401 | `auth.invalid_token` | JWT expired, malformed, or missing |
| 401 | `auth.invalid_credentials` | Wrong email/password |
| 402 | `auth.insufficient_tier` | x402 payment required for premium endpoint |
| 403 | `auth.forbidden` | Insufficient permissions |
| 404 | `not_found` | Resource not found |
| 409 | `auth.email_exists` | Email already registered |
| 422 | `validation.request_invalid` | Pydantic validation error (field details included) |
| 429 | `rate_limit.exceeded` | Too many requests |
| 500 | `internal_error` | Unexpected server error |
| 502 | `provider.unavailable` | External data provider failed |
| 503 | `service_unavailable` | Upstream service unavailable |

### Response Headers

| Header | Description |
|--------|-------------|
| `X-Request-ID` | Correlation ID for every response |
| `X-Response-Time-Ms` | Server-side processing time in ms |
| `X-RateLimit-Limit` | Rate limit ceiling |
| `X-RateLimit-Remaining` | Remaining requests in current window |
| `X-RateLimit-Reset` | Unix timestamp when limit resets |
| `X-Degraded-Stores` | Comma-separated degraded data stores |
| `Deprecation` | `true` on legacy root-level routes |
| `Sunset` | Deprecation date for legacy routes |
| `Server-Timing` | Performance timing header |

---

## 2. Core System

### `GET /health`
Basic health check. Returns `{"status": "ok"}`.

### `GET /live`
Liveness probe. Always returns 200 if process is alive.

### `GET /ready`
Readiness probe. Returns 200 only when all database connections are healthy.

### `GET /version`
Returns running version string.

### `GET /metrics`
Prometheus metrics endpoint (internal monitoring).

### `GET /openapi.json`
OpenAPI 3.1 specification.

### `GET /docs`
Swagger UI documentation.

### `GET /redoc`
ReDoc documentation.

### `GET /.well-known/jwks.json`
JWKS public keys for JWT verification.

---

## 3. Authentication API

All auth endpoints are **rate-limited** to 5 req/min per IP.

### `POST /api/v1/auth/register`
Register a new user account.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "SecurePass123!",
  "display_name": "User Name"
}
```

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "display_name": "User Name",
  "tier": "free",
  "created_at": "2026-07-23T00:00:00Z"
}
```

**Auth:** None

### `POST /api/v1/auth/login`
Authenticate and receive JWT tokens.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "SecurePass123!"
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Auth:** None

### `POST /api/v1/auth/refresh`
Refresh an expired access token.

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response:** `200 OK` (new access token)

**Auth:** None (uses refresh token)

### `POST /api/v1/auth/logout`
Invalidate current session/token.

**Auth:** JWT Bearer

### `POST /api/v1/auth/wallet/nonce`
Get a nonce for wallet signature verification.

**Request:**
```json
{
  "address": "0x...",
  "chain": "ethereum"
}
```

**Response:** `200 OK`
```json
{
  "nonce": "abc123...",
  "message": "Sign this message to authenticate with RMI: abc123..."
}
```

**Auth:** None

### `POST /api/v1/auth/wallet/verify`
Verify wallet signature and receive JWT.

**Request:**
```json
{
  "address": "0x...",
  "signature": "0x...",
  "nonce": "abc123..."
}
```

**Response:** `200 OK` (same token format as login)

**Auth:** None

### `POST /api/v1/auth/wallet/link`
Link a wallet to an existing account.

**Auth:** JWT Bearer

### `POST /api/v1/auth/oauth/{provider}`
OAuth login (Google, GitHub, Discord).

**Auth:** None

---

## 4. Wallet API

### `GET /api/v1/wallet/{address}/balance`
Get wallet balance across multiple chains.

**Auth:** JWT Bearer or API Key  
**Rate limit:** 10 req/min (free), 100 req/min (premium)

**Response:**
```json
{
  "address": "0x...",
  "balances": [
    {"chain": "ethereum", "native": "1.234", "tokens": [{"symbol": "USDC", "balance": "100.00"}]}
  ],
  "cached_at": "2026-07-23T00:00:00Z"
}
```

### `GET /api/v1/wallet/{address}/portfolio`
Full portfolio including PnL.

**Auth:** JWT Bearer  
**Rate limit:** 10 req/min

### `GET /api/v1/wallet/{address}/history`
Transaction history.

**Auth:** JWT Bearer  
**Rate limit:** 10 req/min

### `GET /api/v1/wallet/{address}/risk`
Wallet risk assessment score.

**Auth:** JWT Bearer  
**Rate limit:** 5 req/min (computation-intensive)

### `POST /api/v1/wallet/scan`
Deep wallet risk scan (multi-engine).

**Auth:** JWT Bearer or x402 Payment  
**Rate limit:** 5 req/min (free trial), pay-per-use after

---

## 5. Token API

### `GET /api/v1/token/{address}`
Get token info and metadata.

**Query params:** `chain` (default: solana)

**Auth:** None (public)  
**Rate limit:** 10 req/min

### `GET /api/v1/token/{address}/price`
Real-time token price.

**Auth:** None (public)  
**Rate limit:** 30 req/min  
**Cache TTL:** 8 seconds

### `GET /api/v1/token/{address}/holders`
Holder distribution.

**Auth:** JWT Bearer  
**Rate limit:** 10 req/min

### `POST /api/v1/token/scan`
Security scan a token.

**Auth:** JWT Bearer or x402 Payment  
**Rate limit:** 5 req/min (free trial)

**Response:**
```json
{
  "token_address": "0x...",
  "safety_score": 85,
  "risk_level": "low",
  "risk_flags": [],
  "warnings": [],
  "modules_run": ["honeypot", "deployer", "holders", "market", "social"],
  "confidence": 92,
  "scanned_at": "2026-07-23T00:00:00Z"
}
```

---

## 6. Scanner API

### `POST /api/v2/scanner/token/scan`
Unified v2 token scan (multi-engine fusion).

**Auth:** JWT Bearer or x402 Payment  
**Rate limit:** 5 req/min (free), pay-per-use after

**Request:**
```json
{
  "address": "0x...",
  "chain": "ethereum",
  "engines": ["honeypot", "deployer", "holders", "market", "security", "social"]
}
```

**Response:**
```json
{
  "task_id": "uuid",
  "status": "pending|complete|failed",
  "results": {
    "overall_risk_score": 25,
    "risk_level": "low",
    "confidence": 88,
    "engine_results": {
      "honeypot": {"risk_score": 10, "flags": []},
      "deployer": {"risk_score": 30, "flags": ["new_deployer"]}
    },
    "fusion": {
      "sources_used": 6,
      "sources_degraded": 0,
      "veto_triggered": false
    }
  }
}
```

### `POST /api/v2/scanner/wallet/scan`
Unified wallet scan.

**Auth:** x402 Payment  
**Rate limit:** pay-per-use

---

## 7. x402 Premium Tools

### `GET /api/v1/x402/tools`
List all available premium tools with pricing.

**Auth:** None

### `POST /api/v1/x402/tools/{tool_id}/call`
Call a premium tool. First 1-5 calls are free (per tool), then requires x402 payment.

**Auth:** x402 Payment Proof (after free trial exhausted)

**Request:**
```json
{
  "params": { "tool-specific": "parameters" }
}
```

**Response (free trial available):** `200 OK` with tool result  
**Response (payment required):** `402 Payment Required`
```json
{
  "status": 402,
  "detail": "Payment required for this premium tool",
  "code": "auth.insufficient_tier",
  "payment_url": "https://pay.rugmunch.io/invoice/uuid"
}
```

### `GET /api/v1/x402/pricing`
Current pricing catalog for all paid tools.

**Auth:** None

### `GET /api/v1/x402/usage`
Usage statistics for authenticated user.

**Auth:** JWT Bearer

---

## 8. MCP Protocol

### `POST /mcp/*`
MCP JSON-RPC protocol handler. Accepts standard MCP tool call format.

**Auth:** API Key or x402 Payment  
**Rate limit:** 30 req/min

### `GET /mcp/manifest`
Full platform manifest with live tool counts and capabilities.

**Auth:** None

### `GET /mcp/membership`
Membership tiers, scan packs, streaming feeds, and batch products.

**Auth:** None

### `GET /mcp/skills`
18 agent skills with workflows and anti-abuse rules.

**Auth:** None

### `GET /.well-known/x402`
x402 protocol well-known configuration.

**Auth:** None

---

## 9. News API

### `GET /api/v1/news/feed`
Curated crypto news feed.

**Auth:** None (public)  
**Rate limit:** 10 req/min

### `GET /api/v1/news/headlines`
Latest headlines only.

**Auth:** None

### `GET /api/v1/news/sources`
Available news sources.

**Auth:** JWT Bearer

### `GET /api/v1/news/sentiment`
Market sentiment analysis.

**Auth:** JWT Bearer

### `GET /api/v1/news/categories`
News categories list.

**Auth:** None

### `GET /api/v1/news/articles/{id}`
Get single article details.

**Auth:** None

### `POST /api/v1/news/comment`
Comment on a news article.

**Auth:** JWT Bearer

---

## 10. Reports API

### `POST /api/v1/reports/generate`
Generate an AI investigation report.

**Request:**
```json
{
  "target_address": "0x...",
  "chain": "ethereum",
  "report_type": "full|summary|funding",
  "depth": "standard|deep"
}
```

**Auth:** x402 Payment  
**Processing time:** 30-120 seconds

### `GET /api/v1/reports/{id}`
Get generated report.

**Auth:** JWT Bearer (must own) or API Key

### `GET /api/v1/reports/{id}/citations`
Report citation sources.

**Auth:** JWT Bearer

---

## 11. Labels API

### `GET /api/v1/labels/{address}`
Get all labels for a wallet address.

**Auth:** JWT Bearer  
**Rate limit:** 30 req/min

### `POST /api/v1/labels`
Add a label to a wallet (authenticated users).

**Auth:** JWT Bearer

### `GET /api/v1/labels/search?q={query}`
Search labels by entity name or value.

**Auth:** JWT Bearer

---

## 12. Intelligence & Market

### `GET /api/v1/intelligence/feed`
Threat intelligence feed.

**Auth:** API Key (internal)

### `GET /api/v1/intelligence/latest`
Latest intelligence items.

**Auth:** API Key

### `GET /api/v1/intelligence/crypto-fear-index`
Current Fear & Greed Index.

**Auth:** None

### `GET /api/v1/market/price`
Market price data for multiple tokens.

**Auth:** None (public)

### `GET /api/v1/market/trending`
Trending tokens.

**Auth:** None

### `GET /api/v1/market/gainers` / `GET /api/v1/market/losers`
Top gainers/losers.

**Auth:** None

---

## 13. Security Intelligence

### `GET /api/v1/security/*`
31 security intelligence routes covering:
- Cross-chain correlation
- ML anomaly detection
- On-chain analysis
- MEV signal detection
- Scam school resources

**Auth:** JWT Bearer

---

## 14. Admin & Internal

### `POST /api/v1/admin/alerts/webhook`
Receive alert webhooks from external systems.

**Auth:** API Key (`ADMIN_API_KEY`)

### `GET /api/v1/_test/glitchtip`
Test error tracking integration (generates a test error).

**Auth:** API Key

### `GET /api/v1/status`
System status page.

**Auth:** None (public)

---

## 15. WebSocket

### `WS /ws/alerts`
Real-time alert stream. Connect with JWT or API Key auth.

**Messages:**
```json
// Server → Client
{
  "type": "alert",
  "data": {
    "token_address": "0x...",
    "alert_type": "honeypot_detected",
    "severity": "critical",
    "payload": {}
  }
}

// Client → Server
{
  "type": "subscribe",
  "channels": ["alerts:token:0x...", "alerts:whale"]
}
```

---

## 16. Domain-Specific Endpoints

| Route | Method | Description | Auth |
|-------|--------|-------------|------|
| `/api/v1/bulletin/*` | GET/POST | Bulletin board (21 routes) | JWT |
| `/api/v1/chat/*` | GET/POST | AI chat (5 routes) | JWT |
| `/api/v1/chain-compare/*` | GET | Chain comparison | None |
| `/api/v1/honeypot-map/*` | GET | Honeypot visualization | None |
| `/api/v1/whale-alerts/*` | GET | Whale tracker alerts | JWT |
| `/api/v1/lp-health/*` | GET | LP health monitor | None |
| `/api/v1/ai/*` | GET/SSE | AI streaming endpoints | JWT |
| `/api/v1/rugcharts/*` | GET | RugCharts data | None |
| `/api/v1/rugmaps/*` | GET | RugMaps visualization | None |
| `/api/v1/mev-advisory/*` | GET | MEV risk warnings | None |
| `/api/v1/impersonation/*` | GET | Impersonation detection | JWT |
| `/api/v1/rug-recovery/*` | GET | Rug recovery index | JWT |
| `/api/v1/laundry-matcher/*` | GET | Copycat/laundry detection | JWT |
| `/api/v1/death-clock/*` | GET | Token Death Clock | None |
| `/api/v1/token-cv/*` | GET | On-chain CV | None |
| `/api/v1/tvl-verify/*` | GET | TVL verification | None |
| `/api/v1/contract-analyzer/*` | POST | Contract analysis | JWT |
| `/api/v1/criminal-clusters/*` | GET | Criminal cluster data | API Key |
| `/api/v1/prediction-markets/*` | GET | Prediction markets | None |
| `/api/v1/protect/*` | GET/POST | URL/wallet/token protection | JWT |
| `/api/v1/developer/*` | GET/POST | Developer key management | JWT |
| `/api/v1/rag/v2/search` | POST | Semantic RAG search | JWT |
| `/api/v1/rag/metrics` | GET | RAG observability metrics | API Key |

---

## 17. Legacy (Deprecated) Routes

These root-level routes are **deprecated** and will be removed after **2026-10-17**. Use `/api/v1/*` equivalents.

| Legacy Route | Status | Migration |
|-------------|--------|-----------|
| `/scam-school/*` | ⚠️ Deprecated | Use `/api/v1/scam-school/*` |
| `/rug_probability` | ⚠️ Deprecated | Use `/api/v1/x402/rug-probability` |
| `/history` | ⚠️ Deprecated | Use `/api/v1/x402/history` |
| `/reputation_score` | ⚠️ Deprecated | Use `/api/v1/x402/reputation-score` |
| `/investigation_report` | ⚠️ Deprecated | Use `/api/v1/x402/investigation-report` |
| `/cache/*` | ⚠️ Deprecated | Use `/api/v1/x402/cache/*` |
| `/webhook_register` | ⚠️ Deprecated | Use `/api/v1/x402/webhook-register` |
| `/narrative` | ⚠️ Deprecated | Use `/api/v1/x402/narrative` |

Legacy routes return `Deprecation: true` and `Sunset: Sat, 17 Oct 2026 00:00:00 GMT` headers.
