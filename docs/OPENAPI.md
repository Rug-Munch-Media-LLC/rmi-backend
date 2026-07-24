# OpenAPI Documentation — RMI Enterprise Services

This document describes how to access, configure, and work with OpenAPI
documentation across all RMI backend services.

## Service Inventory

| Service | Port | OpenAPI URL | Swagger UI | Paths | Schemas | Status |
|---------|------|-------------|------------|-------|---------|--------|
| RMI Backend | 8000 | `/openapi.json` | `/docs` | 304 | 65 | ✅ All endpoints documented |
| Pryscraper | 8005 | `/openapi.json` | `/docs` | 186 | 85 | ✅ All endpoints documented |
| WalletPress | 8010 | `/openapi.json` | `/docs` | 112 | 44 | ✅ All endpoints documented |
| rmi-scraper | 8002 | `/openapi.json` | `/docs` | 6 | 2 | ✅ All endpoints documented |
| rmi-crawl4ai | 8004 | `/openapi.json` | `/docs` | 2 | 3 | ✅ All endpoints documented |
| bge-m3 embed | 8001 | `/openapi.json` | `/docs` | 2 | 0 | ✅ All endpoints documented |

## Accessing API Documentation

### Direct Access (local development)

```bash
# RMI Backend
open http://localhost:8000/docs        # Swagger UI
open http://localhost:8000/redoc        # ReDoc
curl http://localhost:8000/openapi.json  # Raw OpenAPI spec

# Pryscraper
open http://localhost:8005/docs

# WalletPress
open http://localhost:8010/docs

# rmi-scraper
open http://localhost:8002/docs

# rmi-crawl4ai
open http://localhost:8004/docs
```

### Via Docker Network

If services are only exposed on the Docker network, use docker exec:

```bash
docker exec rmi-backend curl -s http://127.0.0.1:8000/openapi.json
```

Or port-forward in docker-compose.override.yml:

```yaml
services:
  rmi-backend:
    ports:
      - "8000:8000"
```

### Production / Staging

In production, API docs are available through the nginx reverse proxy:

```
https://api.rugmunch.io/docs
https://pry.rugmunch.io/docs
https://walletpress.rugmunch.io/docs
```

## Authentication Setup for Swagger UI

### RMI Backend

1. Open `/docs` in your browser
2. Click **Authorize** (top-right)
3. Enter one of:
   - **X-API-Key**: Your RMI API key (set via `RMI_AUTH_TOKEN` env var)
   - **Bearer Token**: JWT token obtained from `/api/v1/auth/login`
4. Click **Authorize**

### Pryscraper

1. Open `/docs` in your browser
2. Click **Authorize**
3. Enter your `PRY_API_KEY` as `Bearer {key}`
4. Click **Authorize**

Unauthenticated access is allowed for: `/health`, `/live`, `/ready`, `/docs`, `/openapi.json`.

### WalletPress

1. Open `/docs` in your browser
2. Provide API key via:
   - `Authorization: Bearer {key}` header, or
   - `X-API-Key: {key}` header
3. The admin key (`WP_ADMIN_KEY`) grants full access
4. Team keys can be created with scoped roles: `admin`, `operator`, `viewer`

## Common Patterns and Conventions

### URL Structure

```
/api/v1/{resource}/{action}
/api/v2/{resource}/{action}  (newer endpoints)
```

Root-level routes (e.g., `/health`, `/live`, `/ready`) are infra-only.
See [API_VERSIONING.md](./API.md) for migration from root-level to `/api/v1/*`.

### Response Format

All APIs return JSON. Success responses follow this shape:

```json
{
  "status": "ok",
  "data": { ... }
}
```

Error responses:

```json
{
  "error": "description of error",
  "request_id": "abc123def456"
}
```

### Standard Headers

| Header | Description |
|--------|-------------|
| `X-Request-ID` | Correlation ID for request tracing |
| `X-Response-Time-Ms` | Server processing time in milliseconds |
| `X-RateLimit-Limit` | Rate limit (requests per minute) |
| `X-RateLimit-Remaining` | Remaining requests in current window |
| `X-RateLimit-Reset` | Unix timestamp when limit resets |
| `X-Degraded-Stores` | Lists any degraded backend stores (rmi-backend only) |

### Pagination

List endpoints support cursor-based pagination:

```
GET /api/v1/resource?cursor=abc123&limit=20
```

Response:

```json
{
  "data": [...],
  "next_cursor": "def456",
  "total": 100
}
```

### Versioning

- **v1**: Stable, backward-compatible. Will not break.
- **v2**: Active development. May add fields but will not remove.
- **Root-level**: Deprecated. Migrate to `/api/v1/*` equivalents.

### Rate Limiting

| Service | Default Rate Limit | Header |
|---------|-------------------|--------|
| RMI Backend | 100 req/min | `X-RateLimit-Limit` |
| Pryscraper | 60 req/min | `X-RateLimit-Limit` |
| WalletPress | 60 req/min (configurable) | `X-RateLimit-Limit` |

### Tagging Convention

Each router uses a single tag matching its resource name:

- `health` — health check endpoints
- `scanner-v2` — token/wallet scanning
- `lp-health-monitor` — liquidity pool monitoring
- `mev-sniper` — MEV sniper signals
- `bulletin` — community bulletin board
- `security-intel` — security intelligence
- `wallet-clustering` — wallet clustering and analysis

Tags are defined on the `APIRouter(tags=[...])` constructor and inherited
by all endpoints on that router.

### Security

- All write endpoints require authentication (X-API-Key or Bearer JWT)
- CORS is configured via `CORS_ORIGINS` env var (see below)
- CSRF protection is active for cookie-authenticated requests
- Security headers are set on every response:
  - `Strict-Transport-Security`
  - `X-Content-Type-Options`
  - `X-Frame-Options`
  - `Content-Security-Policy`

## CORS Configuration

CORS must allow Swagger UI access from developer tools and documentation
subdomains. Configure via environment variables:

### RMI Backend

```bash
# Development (allow all local origins)
CORS_ORIGINS='["http://localhost:8000","http://localhost:3000","http://localhost:5173","http://127.0.0.1:8000"]'

# Production (explicit subdomain allowlist)
CORS_ORIGINS='["https://app.rugmunch.io","https://rmi.rugmunch.io","https://docs.rugmunch.io","https://api.rugmunch.io"]'
```

### Pryscraper

Set `PRY_CORS_ORIGINS` or edit the hardcoded list in `api.py`:

```python
allow_origins=[
    "https://rugmunch.io",
    "https://*.rugmunch.io",  # All subdomains
    "http://localhost:3000",
    "http://localhost:5173",
]
```

### WalletPress

```bash
WP_CORS_ORIGINS="http://localhost:8010,https://walletpress.rugmunch.io,https://docs.rugmunch.io"
```

> **Security Note:** In production, never use `["*"]` for CORS origins.
> Always specify an explicit allowlist. The rmi-backend enforces this
> at the application level.

## Testing OpenAPI Documentation

To verify OpenAPI is working correctly:

```bash
# 1. Check OpenAPI spec is valid JSON
curl -s http://localhost:8000/openapi.json | python3 -m json.tool > /dev/null && echo "Valid JSON"

# 2. Check endpoints have descriptions
curl -s http://localhost:8000/openapi.json | python3 -c "
import json,sys
d = json.load(sys.stdin)
missing = sum(1 for p,m in d.get('paths',{}).items() for s in m.values() if isinstance(s,dict) and not s.get('description'))
print(f'Endpoints missing descriptions: {missing}')
"

# 3. Check Swagger UI loads
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs

# 4. Validate against OpenAPI 3.1 schema
pip install -q openapi-spec-validator
python3 -c "from openapi_spec_validator import validate; validate(openapi_spec); print('Spec valid')"
```

## Related Documents

- [API Reference](./API.md) — Full API documentation
- [Architecture Overview](./ARCHITECTURE.md) — System architecture
- [x402 API Reference](./x402_API_REFERENCE.md) — x402 payment protocol
- [TOOLS-REFERENCE.md](./TOOLS-REFERENCE.md) — Tool and provider catalog
