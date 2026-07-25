# API Guide — RMI Backend

> **Quick-start reference for using the RMI API**  
> Full reference: [`API.md`](API.md)  
> Interactive docs: `/docs` (Swagger) | `/redoc` (ReDoc) | `/openapi.json`  

---

## 1. Authentication

### Methods

| Method | Header | Used For |
|--------|--------|----------|
| **JWT Bearer** | `Authorization: Bearer <token>` | User-facing API |
| **API Key** | `X-API-Key: <key>` | Service-to-service |
| **x402 Proof** | `X-Payment-Proof: <tx_hash>` | Premium pay-per-use |
| **Cookie Session** | `csrf_token` + `X-CSRF-Token` | Web UI |

### Get a JWT Token

```bash
# Register
curl -X POST https://api.rugmunch.io/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"SecurePass123!","display_name":"User"}'

# Login
curl -X POST https://api.rugmunch.io/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"SecurePass123!"}'

# Response:
# {"access_token":"eyJ...","refresh_token":"eyJ...","token_type":"bearer","expires_in":3600}
```

---

## 2. Core Endpoints

| Endpoint | Description | Auth | Rate Limit |
|----------|-------------|------|-----------|
| `GET /health` | Basic health | None | 1000/min |
| `GET /live` | Liveness probe | None | 1000/min |
| `GET /ready` | Readiness (DB check) | None | 1000/min |
| `GET /version` | Version info | None | 1000/min |
| `GET /metrics` | Prometheus metrics | Internal | 1000/min |
| `GET /docs` | Swagger UI | None | — |
| `GET /openapi.json` | OpenAPI 3.1 spec | None | — |

---

## 3. Token API

```bash
# Token info (public)
curl https://api.rugmunch.io/api/v1/token/So11111111111111111111111111111111111111112?chain=solana

# Token price (public)
curl https://api.rugmunch.io/api/v1/token/So11111111111111111111111111111111111111112/price

# Token scan (auth required)
curl -X POST https://api.rugmunch.io/api/v1/token/scan \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"address":"0x...","chain":"ethereum"}'
```

---

## 4. Wallet API

```bash
# Wallet balance
curl -H "Authorization: Bearer <jwt>" \
  https://api.rugmunch.io/api/v1/wallet/0x.../balance

# Wallet risk score
curl -H "Authorization: Bearer <jwt>" \
  https://api.rugmunch.io/api/v1/wallet/0x.../risk

# Deep wallet scan (x402)
curl -X POST https://api.rugmunch.io/api/v1/wallet/scan \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"address":"0x..."}'
```

---

## 5. Scanner API (v2)

```bash
# Token scan with all engines
curl -X POST https://api.rugmunch.io/api/v2/scanner/token/scan \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "0x...",
    "chain": "ethereum",
    "engines": ["honeypot", "deployer", "holders", "market", "security", "social"]
  }'
```

---

## 6. x402 Premium Tools

```bash
# List premium tools
curl https://api.rugmunch.io/api/v1/x402/tools

# Call a premium tool (free trial available)
curl -X POST https://api.rugmunch.io/api/v1/x402/tools/{tool_id}/call \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"params": {...}}'

# Check usage
curl -H "Authorization: Bearer <jwt>" \
  https://api.rugmunch.io/api/v1/x402/usage
```

---

## 7. Market Data

```bash
# Market price
curl https://api.rugmunch.io/api/v1/market/price

# Trending tokens
curl https://api.rugmunch.io/api/v1/market/trending

# Fear & Greed Index
curl https://api.rugmunch.io/api/v1/intelligence/crypto-fear-index
```

---

## 8. MCP Protocol

```bash
# Get platform manifest
curl https://api.rugmunch.io/mcp/manifest

# Call an MCP tool
curl -X POST https://api.rugmunch.io/mcp/call/{tool_id} \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"params": {...}}'
```

---

## 9. Error Handling

All errors use [RFC 7807](https://tools.ietf.org/html/rfc7807) Problem Details format:

```json
{
  "type": "https://api.rugmunch.io/errors/invalid-token",
  "title": "Invalid authentication token",
  "status": 401,
  "detail": "JWT expired or malformed",
  "instance": "/api/v1/token/scan"
}
```

### Common Error Codes

| Status | Code | Meaning |
|--------|------|---------|
| 400 | `validation.request_invalid` | Validation failed |
| 401 | `auth.invalid_token` | JWT expired/malformed/missing |
| 402 | `auth.insufficient_tier` | x402 payment required |
| 403 | `auth.forbidden` | Insufficient permissions |
| 429 | `rate_limit.exceeded` | Too many requests |
| 500 | `internal_error` | Unexpected server error |

---

## 10. Response Headers

Every response includes:

| Header | Example | Description |
|--------|---------|-------------|
| `X-Request-ID` | `abc-123-def` | Correlation ID |
| `X-Response-Time-Ms` | `45.2` | Server processing time |
| `X-RateLimit-Limit` | `100` | Rate limit ceiling |
| `X-RateLimit-Remaining` | `87` | Remaining requests |
| `X-RateLimit-Reset` | `1721843200` | Reset Unix timestamp |
| `X-Degraded-Stores` | `birdeye,helius` | Unhealthy data sources |
| `Deprecation` | `true` | Legacy route warning |
| `Sunset` | `Sat, 17 Oct 2026` | Legacy route removal date |

---

## 11. WebSocket

```bash
# Connect to alert stream
wscat -c wss://api.rugmunch.io/ws/alerts \
  -H "Authorization: Bearer <jwt>"

# Subscribe to channels
{"type":"subscribe","channels":["alerts:token:0x...","alerts:whale"]}
```

---

## Rate Limit Tiers

| Tier | Rate | Daily Cap | Auth Required |
|------|------|-----------|---------------|
| Anonymous | 10 req/min | — | None |
| Free | 10 req/min | 100/day | API key or JWT |
| Pro | 60 req/min | 10K/day | JWT |
| Enterprise | 300 req/min | Custom | Custom |

---

*Full API reference: [`API.md`](API.md) (657 lines, 19 sections)*  
*Interactive: https://api.rugmunch.io/docs*  
*Spec: https://api.rugmunch.io/openapi.json*
