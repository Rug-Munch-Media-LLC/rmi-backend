# Unified Authentication System — SSO Across All RMI Services

## Overview

All four RMI services (rugmunch.io, walletpress, pryscraper, degenfeed) now
share a single authentication domain. Users authenticate once via rmi-backend
and their JWT is accepted by all services.

## Architecture

```
┌──────────────┐   JWT (HS256 in production) ┌──────────────┐
│              │ ◄───────────────────────── │  rmi-backend │
│  walletpress │     Auth: Bearer <jwt>     │  (rugmunch)  │
│              │                            │              │
│  pryscraper  │ ◄───────────────────────── │  Issues JWT  │
│              │     Auth: Bearer <jwt>     │  HS256-signed│
│              │                            │              │
│  degenfeed   │ ◄───────────────────────── │  Serves JWKS │
│              │     Cookie / Bearer        │ /.well-known │
└──────────────┘                            └──────────────┘
```

### Token Flow

1. User logs in at rugmunch.io (email, wallet, Google, GitHub, Telegram)
> ⚠️ **The diagram above is the target state, not what runs.** Production
> signs **HS256**, so step 4's JWKS route is dead: both JWKS endpoints
> return `{"error":"JWKS only available for RS256","algorithm":"HS256"}`.
> `pryscraper` (`src/pry/core/jwt_auth.py`) is RS256-only and fail-closed,
> so it **cannot verify an rmi-backend token today**; there is no
> shared-secret path on the pry side.

2. rmi-backend issues an HS256-signed JWT (`JWT_ALGORITHM=HS256` in production)
3. JWT is stored as `rmi_token` HttpOnly cookie + returned in response body
4. All services accept this JWT via:
   - `Authorization: Bearer <jwt>` header (API clients)
   - `rmi_token` HttpOnly cookie (browser clients)

### JWT Payload

```json
{
  "sub": "a1b2c3d4e5f6...",       // User ID
  "email": "user@example.com",      // Email
  "tier": "FREE",                   // FREE | PRO | ENTERPRISE
  "role": "USER",                   // USER | ADMIN | MODERATOR
  "wallet": "0x...",               // Optional wallet address
  "iat": 1712345678,               // Issued at
  "exp": 1712950478                // Expires (7 days)
}
```

## Service Configuration

### 1. rmi-backend (already configured)

The JWT system is already live. Public key is served at:

```
GET https://rugmunch.io/.well-known/jwks.json
GET https://rugmunch.io/api/v1/auth/jwks
```

**Env vars:**
- `JWT_ALGORITHM=RS256` — asymmetric signing
- `JWT_PRIVATE_KEY` — RSA private key for signing
- `JWT_PUBLIC_KEY` — RSA public key for verification
- `JWT_SECRET` — fallback HS256 secret

### 2. walletpress

**New env vars:**
- `WP_JWT_ALGORITHM=RS256` (default) or `HS256`
- `WP_JWT_PUBLIC_KEY` — RSA public key PEM (or file path)
- `WP_JWKS_URL=https://rugmunch.io/.well-known/jwks.json` — auto-fetch
- `WP_JWKS_CACHE_TTL=3600` — seconds to cache the JWKS response

**Auth order:**
1. `WP_ADMIN_KEY` env var (legacy) — full admin access
2. `X-API-Key` header — WalletPress API keys (existing)
3. `Authorization: Bearer <jwt>` — JWT from rmi-backend (NEW)

The JWT middleware (`core/jwt_auth.py`) runs alongside the existing
`require_auth_on_mutations` middleware. JWT-authenticated requests
receive a `request.state.jwt_user` dict with claims.

**User mapping:** JWT `sub` → walletpress user context. The user ID
from rmi-backend is available as `request.state.jwt_user["id"]`.

### 3. pryscraper

**New env vars:**
- `PRY_JWT_ALGORITHM=RS256` (default) or `HS256`
- `PRY_JWT_PUBLIC_KEY` — RSA public key PEM (or file path)
- `PRY_JWKS_URL=https://rugmunch.io/.well-known/jwks.json`
- `PRY_JWKS_CACHE_TTL=3600`

**Auth order:**
1. `PRY_API_KEY` env var (legacy) — single shared key
2. `Authorization: Bearer <jwt>` — JWT from rmi-backend (NEW)

The middleware (`jwt_auth.py`) is integrated into `PryHttpMiddleware` (api.py).

### 4. degenfeed-web (Next.js)

**New env vars:**
- `NEXT_PUBLIC_RMI_JWKS_URL=https://rugmunch.io/.well-known/jwks.json`
- `RMI_JWT_PUBLIC_KEY` — RSA public key PEM (offline validation)
- `RMI_JWT_ALGORITHM=RS256` (default) or `HS256`
- `RMI_JWT_SECRET` — for HS256 mode only

**Auth order:**
1. `Authorization: Bearer <jwt>` — JWT from rmi-backend (NEW, tried first)
2. `__Host-dfid` cookie — existing wallet HMAC session (legacy)

The JWT verification (`lib/jwt-auth.ts`) uses Web Crypto API (edge-compatible).

## Migration Plan

### Phase 1: Core (Done)
- ✅ Shared JWT validation library (`app/auth/jwt_validation.py`)
- ✅ JWKS endpoint (`/.well-known/jwks.json`)
- ✅ Registration in mount.py

### Phase 2: walletpress (Done)
- ✅ `core/jwt_auth.py` — JWT validation middleware
- ✅ Registered in main.py middleware stack
- ✅ Dual-mode: WP_ADMIN_KEY + JWT

### Phase 3: pryscraper (Done)
- ✅ `jwt_auth.py` — JWT validation module
- ✅ Modified PryHttpMiddleware for dual-mode auth
- ✅ JWT detection (3-segment heuristic) vs API key

### Phase 4: degenfeed (Done)
- ✅ `lib/jwt-auth.ts` — Edge-compatible JWT verification
- ✅ Modified middleware.ts for dual-mode auth
- ✅ JWT first, wallet HMAC fallback

### Phase 5: Verification (TODO)
- [ ] Test JWT validation in walletpress
- [ ] Test JWT validation in pryscraper
- [ ] Test JWT validation in degenfeed
- [ ] End-to-end SSO flow test

## Testing

### Manual Test Flow

```bash
# 1. Get a JWT from rmi-backend (login)
curl -X POST https://rugmunch.io/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "..."}'

# Returns: {"access_token": "eyJ...", "user": {...}}

# 2. Use the JWT to access walletpress
curl -X GET https://walletpress.rugmunch.io/api/v1/wallets \
  -H "Authorization: Bearer eyJ..."

# 3. Use the JWT to access pryscraper
curl -X POST https://pry.rugmunch.io/v1/scrape \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# 4. Use the JWT to access degenfeed
curl -X POST https://degenfeed.xyz/api/publish \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello from SSO!"}'
```

### JWT Debugging

```bash
# Decode a JWT without verification (for debugging)
echo 'eyJ...' | cut -d. -f2 | base64 -d 2>/dev/null || true

# Fetch JWKS
curl https://rugmunch.io/.well-known/jwks.json | jq .
```

## Security Notes

- All JWTs are RS256-signed by default (asymmetric)
- HS256 fallback exists for backward compatibility
- Service-to-service calls should continue using API keys
- JWT tokens expire after 7 days (configurable)
- The `rmi_token` cookie is HttpOnly + Secure + SameSite=Strict
- Legacy auth methods (WP_ADMIN_KEY, PRY_API_KEY) remain functional
- JWKS is cached for 1 hour to reduce network calls
