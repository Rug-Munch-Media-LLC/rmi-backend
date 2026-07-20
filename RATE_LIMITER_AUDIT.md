# Rate Limiter Audit — rmi-backend

**Audit date:** 2026-07-20

---

## Summary

**7 rate limiter implementations found** in the rmi-backend codebase. Of these, only **2 are active** — the rest are deprecated and should be removed after confirming no remaining consumers.

---

## Rate Limiter Inventory

### ACTIVE

| # | File | Purpose | Status |
|---|------|---------|--------|
| 1 | `app/core/rate_limiter.py` | **UnifiedRateLimiter** — Redis-backed token bucket algorithm. Consolidates all 7 previous implementations. Supports: FastAPI middleware, FastAPI dependency, standalone acquire, path-based routing, per-provider rate limiting, 3-tier pricing (FREE/PRO/ENTERPRISE), PII-safe hash. This is the canonical rate limiter. | **ACTIVE** — The designated replacement for all deprecated files. |
| 2 | `app/core/slowrate.py` | **slowapi Limiter** — Lightweight per-IP rate limiter imported by `middleware_setup.py` for auth endpoint brute-force protection. Uses `slowapi` library with `@limiter.limit(...)` decorators on sensitive endpoints (login, register, 2FA). | **ACTIVE** — Complementary to UnifiedRateLimiter; handles coarse per-IP auth throttling. |

### DEPRECATED (use UnifiedRateLimiter instead)

| # | File | Purpose | Deprecation Marker |
|---|------|---------|--------------------|
| 3 | `app/middleware/rate_limit.py` | **RateLimiter** — Redis-backed tier-based rate limiter (FREE/BASIC/PREMIUM/ENTERPRISE). Used by old middleware layer. | ✅ Has header: `# DEPRECATED: Use app.core.rate_limiter (UnifiedRateLimiter) instead.` |
| 4 | `app/caching_shield/rate_limiter.py` | **RpcRateLimiter** — Token bucket for RPC provider calls (Helius, QuickNode, Alchemy, dRPC, PublicNode). Per-provider and per-method limits (e.g. getProgramAccounts: 5/s). Used by `caching_shield/` RPC routing layer. | ✅ Has header: `# DEPRECATED: Use app.core.rate_limiter (UnifiedRateLimiter) instead.` |
| 5 | `app/scan_rate_limiter.py` | **Scan Rate Limiter** — Freemium scan limiter (5 free/day, premium tiers). Used to limit token/wallet scans per identity. Separate Redis DB. Returns upsell messaging on limit hit. | ✅ Has header: `# DEPRECATED: Use app.core.rate_limiter (UnifiedRateLimiter) instead.` |
| 6 | `app/core/rate_limit_config.py` | **RateLimitRule config** — Contains `RULES` and `PATH_CATEGORIES` for slowapi rate limiting. The path categories are duplicated in `app/core/rate_limiter.py`. | ⚠️ Standalone config file; values are duplicated into the active `app/core/rate_limiter.py`. Should be consolidated or deleted. |

### NOT A RATE LIMITER (different problem)

| # | File | Purpose | Assessment |
|---|------|---------|------------|
| 7 | `app/rate_limiter.py` | **EmbeddingDispatcher** — Rate-limit-aware multi-provider dispatcher for AI EMBEDDINGS, not API request rate limiting. Tracks usage across Gemini, Mistral, NVIDIA, OpenRouter, local BGE, and hash fallback. Routes embedding requests to the provider with most remaining capacity. | **KEEP** — This solves a different problem (embedding load balancing with provider fallback). Poorly named file; should be renamed to `app/embedding_dispatcher.py` to avoid confusion. |

### Test coverage

| File | Notes |
|------|-------|
| `tests/unit/test_rate_limiter_config.py` | Tests for `rate_limit_config.py` |
| `tests/unit/test_rate_limiter_pii_hash.py` | Tests for PII hash utilities in `core/rate_limiter.py` |
| `tests/unit/test_config.py` | References rate limit config values |

---

## File Details

### 1. `app/core/rate_limiter.py` (ACTIVE — 769 lines)
- **Implementation:** `UnifiedRateLimiter` class using Redis token bucket (Lua script) with in-memory fallback.
- **Dependencies:** Redis, FastAPI middleware/dependency system.
- **Features:**
  - Token bucket algorithm with configurable rate/burst per path category.
  - 10 path categories: health, auth, x402, scan, ai, market, public, mcp, admin, default.
  - Path-to-category mapping via `PATH_CATEGORIES` dict.
  - Backward-compatible `acquire_provider()` for legacy RpcRateLimiter consumers.
  - PII-safe HMAC hashing for client identity keys.
  - X-RateLimit-Limit / X-RateLimit-Remaining / X-RateLimit-Reset headers on every response.
  - Retry-After header on 429 responses.
  - FastAPI middleware (`RateLimitMiddleware`) + FastAPI dependency (`RateLimitDependency`).
  - 3-tier pricing router (FREE/PRO/ENTERPRISE) with usage tracking and upgrade endpoints.
  - In-memory fallback when Redis is down.
- **Singleton:** `get_rate_limiter()`.

### 2. `app/core/slowrate.py` (ACTIVE — 13 lines)
- **Implementation:** `slowapi.Limiter` with `get_remote_address` key function.
- **Dependencies:** `slowapi` library.
- **Purpose:** Simple per-IP rate limiting for login/register/2FA endpoints.
- **Wired in:** `app/middleware_setup.py` → registers `SlowAPIMiddleware` + 429 handler.

### 3. `app/middleware/rate_limit.py` (DEPRECATED — 95 lines)
- **Implementation:** `RateLimiter` class with Redis INCR/EXPIRE for tier-based rate limiting.
- **Limits:** FREE (10 rph, 100 rpd), BASIC (50 rph, 500 rpd), PREMIUM (200 rph, 2000 rpd), ENTERPRISE (1000 rph, 10000 rpd).
- **Known issue:** Fail-open on Redis error (`return True`).

### 4. `app/caching_shield/rate_limiter.py` (DEPRECATED — 286 lines)
- **Implementation:** `RpcRateLimiter` class with Redis Lua token bucket.
- **Purpose:** Rate-limit RPC provider calls (Helius 20/s, QuickNode 20/s, Alchemy 20/s, dRPC 12/s, PublicNode 8/s, etc.).
- **Features:**
  - Per-method limits (e.g., `getProgramAccounts`: 5/s).
  - Redis Lua script for atomic token consumption.
  - In-memory fallback.
  - Stats endpoint for bucket state inspection.

### 5. `app/scan_rate_limiter.py` (DEPRECATED — 382 lines)
- **Implementation:** Simple Redis INCR-based daily counter.
- **Purpose:** Limit free tier scans to 5/day per identity (IP or user ID).
- **Features:**
  - Per-tier limits: Free (5/day), Pro (100/day), Elite (unlimited).
  - x402 paid calls bypass limits.
  - Internal API key bypass.
  - Trial abuse prevention with device fingerprinting.
  - Premium upsell messaging with pricing tiers.
  - Scan pack marketplace (Starter 50/day $29.99, Pro 200/day $49.99, Elite 1000/day $89.99).
  - Bot API packs (Scout 200 calls $9.99, Hunter 1500 calls $49.99, Whale 10000 calls $199.99).

### 6. `app/core/rate_limit_config.py` (CONFIG FILE — 66 lines)
- **Purpose:** `RateLimitRule` dataclass and lists of `RULES` + `PATH_CATEGORIES`.
- **Note:** Path categories are identical to those in `app/core/rate_limiter.py`. This file was consumed by `slowapi` and is now a loose reference. The `PATH_CATEGORIES` dict can be deleted from here once `slowrate.py` is migrated to use `app/core/rate_limiter.py`'s config.

### 7. `app/rate_limiter.py` (KEEP — 707 lines)
- **Implementation:** `EmbeddingDispatcher` with `RateTracker` (Redis + memory) + `EmbeddingCache`.
- **Purpose:** Smart routing across 10+ embedding providers (Gemini ×3, Vertex AI, Mistral, NVIDIA via OpenRouter, NVIDIA Direct, BGE-M3, local BGE-small, hash fallback).
- **Not a rate limiter** — it's an embedding load balancer with rate awareness.
- **Should be renamed** to avoid confusion with the real rate limiter.

---

## Known Issues

### 1. Fail-Open Behavior
- `app/middleware/rate_limit.py` line 76: `return True, limit  # Fail open if Redis down`
- `app/scan_rate_limiter.py` line 258: `# On Redis failure, allow the scan (fail open, not closed)`
- `app/core/rate_limiter.py` lines 239-243: Gracefully falls back to in-memory mode (acceptable).
- **Risk:** During a Redis outage, all deprecated limiters silently allow unlimited requests.

### 2. Configuration Drift
- `PATH_CATEGORIES` is duplicated in `app/core/rate_limiter.py` and `app/core/rate_limit_config.py` with identical values.
- The deprecated `app/middleware/rate_limit.py` has a completely different tier system (FREE/BASIC/PREMIUM/ENTERPRISE) than the active one (FREE/PRO/ENTERPRISE).
- `app/scan_rate_limiter.py` uses `REDIS_USAGE_DB=2` while the active limiter uses `RATE_LIMITER_REDIS_DB=4`.
- Each deprecated limiter has its own Redis connection configuration.

### 3. Dual Active Systems
- Both `app/core/rate_limiter.py` and `app/core/slowrate.py` are active simultaneously.
- `slowrate` handles auth endpoints via slowapi decorators; `UnifiedRateLimiter` handles everything else via middleware.
- This dual approach may cause confusion about which limiter applies to a given endpoint.

### 4. Misleading File Name
- `app/rate_limiter.py` is not a rate limiter — it's an embedding provider dispatcher. Its name causes confusion and makes it appear in rate limiter search results.

---

## Recommended Consolidation Plan

### Phase 1 — Remove deprecated files (after confirming zero consumers)
1. **`app/middleware/rate_limit.py`** — Delete. Confirm no imports from this file remain.
2. **`app/caching_shield/rate_limiter.py`** — Delete. Consumers should use `UnifiedRateLimiter.acquire_provider()`.
3. **`app/scan_rate_limiter.py`** — Delete. Scan-specific daily limits should be configured through `UnifiedRateLimiter`'s `scan` path category (20/min default) plus custom tier logic.

### Phase 2 — Config consolidation
4. **`app/core/rate_limit_config.py`** — Delete. The `PATH_CATEGORIES` and rules are already duplicated in `app/core/rate_limiter.py`. If `slowrate.py` needs these, import them from the active limiter instead.

### Phase 3 — Cleanup
5. **`app/rate_limiter.py`** — Rename to `app/embedding_dispatcher.py` and update all internal imports.
6. **`tests/unit/test_rate_limiter_config.py`** — Update to target `app/core/rate_limiter.py` config instead.

### Phase 4 — Hardening
7. Remove fail-open behavior from all limiters. Fail-closed (reject on Redis failure) after a configurable grace period.
8. Add health check endpoint for rate limiter state.

---

## Migration Status

| File | Status | Action Needed |
|------|--------|---------------|
| `app/core/rate_limiter.py` | ✅ Active | Keep |
| `app/core/slowrate.py` | ✅ Active | Keep (complementary) |
| `app/middleware/rate_limit.py` | 🟡 Deprecated | Verify no consumers → Delete |
| `app/caching_shield/rate_limiter.py` | 🟡 Deprecated | Verify no consumers → Delete |
| `app/scan_rate_limiter.py` | 🟡 Deprecated | Verify no consumers → Delete |
| `app/core/rate_limit_config.py` | 🟡 Duplicate | Delete (data in active file) |
| `app/rate_limiter.py` | 🔵 Misnamed | Rename to `app/embedding_dispatcher.py` |
