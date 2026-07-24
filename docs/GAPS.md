# RMI System — Comprehensive Gaps Document

**Generated:** 2026-07-24  
**Scope:** `/srv/work/repos/rmi-backend` (988 Python files, 245 `app/` entries, 115 router files, 131 test files)  
**Methodology:** Static analysis of source tree, TODO/FIXME markers, endpoint stubs, test coverage map, docker-compose, env config, and mount configuration.

---

## Priority Legend

| Mark | Meaning |
|------|---------|
| **CRITICAL** | Blocking production reliability, security, or revenue |
| **HIGH** | Significant risk or missing capability — should fix within 1 sprint |
| **MEDIUM** | Important but not urgent — address in 2-3 sprints |
| **LOW** | Nice-to-have, technical debt, or aspirational |

---

## 1. FUNCTIONALITY GAPS

### 1.1 Stubbed / 501 Not Implemented Endpoints

| Endpoint | File | Priority | Notes |
|----------|------|----------|-------|
| `POST /api/v1/auth/alerts` | `app/api/v1/auth/alerts.py:52` | **HIGH** | Alert CRUD is fully stubbed — returns 501 for create, delete |
| `DELETE /api/v1/auth/alerts/{rule_id}` | `app/api/v1/auth/alerts.py:65` | **HIGH** | No alert deletion |
| `POST /api/v1/admin/alerts/webhook` | `app/api/v1/admin/alerts_webhook.py:27` | **HIGH** | "Alert webhook ingestion not yet implemented - pending T08 GlitchTip wiring" |
| `POST /databus/webhook` | `app/databus/router/webhooks.py:23,48,64` | **MEDIUM** | "Webhook system not available" for all webhook operations |
| `GET /api/v1/public/wallet/{address}` | `app/api/v1/public/wallet.py:41` | **MEDIUM** | Wallet resolve endpoint returns 501 |
| `GET /api/v1/public/wallet/{address}/labels` | `app/api/v1/public/wallet.py:50` | **MEDIUM** | Returns 501 |
| `GET /api/v1/public/wallet/{address}/history` | `app/api/v1/public/wallet.py:59` | **MEDIUM** | Returns 501 |
| `POST /api/v1/public/scanner/scan` | `app/api/v1/public/scanner.py:60` | **MEDIUM** | Returns 501 |
| `GET /api/v1/public/token/{address}` | `app/api/v1/public/token.py:51` | **MEDIUM** | Returns 501 |
| `GET /api/v1/public/token/{address}/risk` | `app/api/v1/public/token.py:60` | **MEDIUM** | Returns 501 |
| `GET /api/v1/public/token/{address}/holders` | `app/api/v1/public/token.py:69` | **MEDIUM** | Returns 501 |

**Count: 11 stubbed endpoints** — the entire public API surface is non-functional.

### 1.2 Dormant / Unmounted Routers

| Router | File | Priority | Notes |
|--------|------|----------|-------|
| `admin_extensions` | `app/routers/admin_extensions.py` (907 lines, 25 routes) | **HIGH** | DORMANT per mount.py: "darkroom-specific, would shadow /api/v1/admin/alerts_webhook" |
| `x402.tools_router` | `app/domains/x402/tools_router` | **HIGH** | `TODO(mount)` in mount.py:134 — paid tools not wired |
| `unified_wallet_scanner` | `app/routers/unified_wallet_scanner` | **MEDIUM** | DEFERRED in Wave 3: "NO `router` APIRouter attribute, consumed by unified_scanner_router" |
| `scam_school` (legacy path) | `app/routers/scam_school` | **LOW** | "DEPRECATED — use /api/v1/scam-school/\*" |

### 1.3 Abstract / Unimplemented Base Classes

| Base Class | File | Priority | Notes |
|------------|------|----------|-------|
| `BillingFacilitatorBase` | `app/domains/billing/facilitators/base.py` | **CRITICAL** | 4 abstract methods: `create_payment`, `verify_payment`, `refund_payment`, `get_balance` — no concrete implementations wired |
| `TokenDeployerBase` | `app/domains/tokens/deployer/base.py` | **HIGH** | 15 abstract methods — no concrete deployer implementations |
| `PortfolioRiskConnector` | `app/domains/portfolio/portfolio_risk_package/connectors.py` | **HIGH** | `get_price`, `get_balance` both raise NotImplementedError |
| `PortfolioRiskAggregatorConnector` | `app/domains/portfolio/portfolio_risk_aggregator/connectors.py` | **HIGH** | Same — stubbed |
| `BaseService` | `app/core/base_service.py` | **MEDIUM** | 4 abstract methods — generic base |

### 1.4 TODO/FIXME Markers (24 total, 15 non-trivial)

**Top 10 most important TODOs:**

| # | File | Line | TODO | Priority |
|---|------|------|------|----------|
| 1 | `app/domains/billing/x402/shared.py` | 462 | `record_x402_payment` is a logging stub — no actual payment recording | **CRITICAL** |
| 2 | `app/mcp/server.py` | 588 | "M3 moat TIER 2 - add API key check before opening to public" | **CRITICAL** |
| 3 | `app/detectors/flash_loan/algorithms/__init__.py` | 338 | "Replace with DataBus / price_consensus module call for production" | **HIGH** |
| 4 | `app/routers/bulletin_board.py` | 564 | "Implement comment moderation" | **HIGH** |
| 5 | `app/agents/tools.py` | 21 | "Wire to actual scanner" | **HIGH** |
| 6 | `app/api/v1/auth/alerts.py` | 46 | "wire up to Postgres once auth context is established" | **HIGH** |
| 7 | `app/domains/labels/sources/chainbase.py` | 3 | "add CHAINBASE_API_KEY to gopass + env" | **MEDIUM** |
| 8 | `app/domains/labels/sources/open_labels.py` | 3 | "wire up when OLI endpoint is confirmed" | **MEDIUM** |
| 9 | `app/cross_encoder_reranker.py` | 148 | "pin to specific revision SHA for supply-chain integrity" | **MEDIUM** |
| 10 | `app/contract_deepscan.py` | 20 | "migrate _fetch_source_code to async httpx.AsyncClient" | **MEDIUM** |

### 1.5 Additional Known Functional Gaps

| Gap | Evidence | Priority |
|-----|----------|----------|
| `GoPlus` integration missing | `find app -name "*goplus*"` returns nothing; `scam_sources.py` exists but GoPlus is referenced only in threat_feeds.py stub | **HIGH** |
| Databus provider implementations | `app/domains/databus/providers/impl/` directory is EMPTY (no `.py` files) | **HIGH** |
| Flash loan detector uses ad-hoc price | Comment says "Replace with DataBus / price_consensus module" — not wired to price_consensus.py | **MEDIUM** |
| x402 payment recording is a stub | `app/domains/billing/x402/shared.py:449-462` explicitly calls itself a "P3A stub" | **CRITICAL** |
| MCP server lacks auth | `app/mcp/server.py:588` — no API key check before public exposure | **CRITICAL** |

---

## 2. INTEGRATION GAPS

### 2.1 Services Not Fully Connected

| Service | Status | Priority | Notes |
|---------|--------|----------|-------|
| **Flash loan detector → price_consensus** | Not wired | **HIGH** | Still using hardcoded fallback prices |
| **Labels pipeline → Postgres** | Not wired | **HIGH** | `internal_labels.py` TODO: "query Postgres internal_labels table once schema lands" |
| **Labels pipeline → Chainbase** | Not wired | **MEDIUM** | `chainbase.py` TODO: "add CHAINBASE_API_KEY" |
| **Labels pipeline → OLI** | Not wired | **LOW** | `open_labels.py` TODO: "wire up when OLI endpoint is confirmed" |
| **x402 payments → actual ledger** | Not wired | **CRITICAL** | Stub only, no real payment recording |
| **Webhook system → external monitors** | Not wired | **HIGH** | All webhook endpoints return 501 |
| **Alert system → Postgres** | Not wired | **HIGH** | Stub awaiting auth context |
| **Databus provider impl layer** | Empty directory | **HIGH** | `app/domains/databus/providers/impl/` has zero files |

### 2.2 Sync HTTP Clients (Blocking Event Loop)

These modules use synchronous `httpx.get()`/`httpx.post()` in what should be an async codebase — blocks the event loop under load:

| File | Priority | Notes |
|------|----------|-------|
| `app/contract_deepscan.py` | **HIGH** | Uses `httpx.get()` with explicit TODO to migrate |
| `app/blockchair_connector.py` | **HIGH** | Entire class is sync httpx |
| `app/defillama_connector.py` | **HIGH** | Entire class is sync httpx |
| `app/nansen_connector.py` | **HIGH** | Entire class is sync httpx |
| `app/apify_tools.py` | **MEDIUM** | Sync httpx.post/get |
| `app/adapters/binance_web3.py` | **MEDIUM** | Sync httpx |
| `app/threat_intel.py` | **MEDIUM** | Sync httpx |
| `app/routers/bulletin.py` | **MEDIUM** | Sync httpx calls in route handlers |
| `app/databus/global_news.py` | **MEDIUM** | Sync |
| `app/databus/mega_news.py` | **MEDIUM** | Sync |
| `app/databus/mega_scraper.py` | **MEDIUM** | Sync |
| `app/databus/social_feeds.py` | **MEDIUM** | Sync |
| `app/databus/x402_mcp_server.py` | **MEDIUM** | Sync |
| `app/core/agent_memory.py` | **LOW** | Sync httpx (used in agent context, not hot path) |

**Count: 14 files with synchronous HTTP calls in async context.**

### 2.3 Event Flows That Are Broken / Gapped

| Flow | Gap | Priority |
|------|-----|----------|
| Scanner alert → webhook → external monitor | Webhook endpoint is 501 | **HIGH** |
| x402 payment → payment recording → ledger | Payment recording is a stub | **CRITICAL** |
| New token detection → alert dispatch → Telegram/email | Alert router is all stubs | **HIGH** |
| Label source ingestion → wallet_memory | Chainbase/OLI/internal not wired | **MEDIUM** |
| Flash loan detection → price consensus feed | Not wired to price_consensus module | **MEDIUM** |

---

## 3. TESTING GAPS

### 3.1 Coverage Summary

| Test Tier | Count | Coverage |
|-----------|-------|----------|
| Unit | 56 files | ~6% of 988 Python files |
| Integration | 24 | ~2.4% |
| E2E | 4 | ~0.4% |
| Security | 6 | ~0.6% |
| Contract | 4 | ~0.4% |
| Benchmark | 6 | ~0.6% |
| **Total** | **131** | **~13% of files** |

### 3.2 Entire Domains with Zero Tests

| Domain | Files | Priority | Key Modules |
|--------|-------|----------|-------------|
| `app/domains/labels/` | 14 | **CRITICAL** | All label sources (metasleuth, chainbase, internal, open_labels, clickhouse) |
| `app/domains/x402/` | 12 | **CRITICAL** | x402 billing, tools, integration — payment-critical code |
| `app/domains/prediction/` | 12 | **HIGH** | Prediction market service + package |
| `app/domains/tokens/` | 8 | **HIGH** | Token deployer base and related |
| `app/domains/analysis/` | 6 | **HIGH** | Liquidation analysis |
| `app/domains/airdrop/` | 6 | **HIGH** | Multichain airdrop engine |
| `app/domains/alerts/` | 4 | **HIGH** | Alert system |
| `app/domains/reports/` | 3 | **HIGH** | Report generation |
| `app/domains/threat/` | 1 | **HIGH** | Threat intelligence |

### 3.3 Largest Untested Files (800+ lines, zero tests)

| File | Lines | Priority | Description |
|------|-------|----------|-------------|
| `app/routers/admin_extensions.py` | 907 | **HIGH** | 25 admin routes — dormant but still in tree |
| `app/bulletin_board.py` | 904 | **HIGH** | Bulletin board system |
| `app/routers/x402_middleware.py` | 896 | **CRITICAL** | x402 payment middleware — revenue-critical |
| `app/routers/scam_school/content.py` | 894 | **MEDIUM** | Scam school content module |
| `app/domains/scanner/rugmaps/engine.py` | 893 | **HIGH** | RugMaps engine — core product |
| `app/domains/billing/x402/shared.py` | 881 | **CRITICAL** | x402 shared billing — revenue-critical |
| `app/wallet_memory/ingestion.py` | 868 | **HIGH** | Wallet memory data ingestion |
| `app/routers/x402_enrichment.py` | 862 | **CRITICAL** | x402 enrichment — revenue-critical |
| `app/domains/billing/x402/tools/token_tools.py` | 860 | **CRITICAL** | x402 token tools — revenue-critical |
| `app/databus/news_intel.py` | 858 | **MEDIUM** | News intelligence pipeline |
| `app/db_client.py` | 854 | **HIGH** | Database client — data layer foundation |
| `app/core/data_fusion.py` | 847 | **HIGH** | Data fusion engine |
| `app/dex_pool_manipulation_analyzer.py` | 837 | **HIGH** | DEX pool manipulation analysis |
| `app/launch_fairness_analyzer.py` | 836 | **HIGH** | Launch fairness analysis |
| `app/domains/scanner/modules/market.py` | 827 | **HIGH** | Market scanner module |

### 3.4 Features with Inadequate Test Coverage

| Feature | Test Status | Priority |
|---------|-------------|----------|
| **Wallet clustering / entity resolution** | No tests found | **HIGH** |
| **Threat intel feeds** | Zero tests | **HIGH** |
| **RAG pipeline** | Only `tests/manual/test_rag.py` (manual) | **HIGH** |
| **Price consensus engine** | Zero tests | **HIGH** |
| **Bridge health monitor** | Tests exist (2 unit) | **OK** |
| **All 9 detector classes** | Tests exist | **OK** |
| **Cross-chain trace/whale** | Tests exist (2 unit) | **OK** |

---

## 4. SECURITY GAPS

### 4.1 Endpoints Missing Rate Limiting

The following router files have NO rate limiting middleware applied:

| Router | Priority | Notes |
|--------|----------|-------|
| `app/routers/x402_enrichment.py` | **CRITICAL** | Revenue endpoint — unlimited requests |
| `app/routers/x402_middleware.py` | **CRITICAL** | Payment middleware — unlimited |
| `app/routers/x402_advanced_tools/` (all 7 files) | **CRITICAL** | Paid tools — unlimited |
| `app/routers/x402_databus_tools/` (all 7 files) | **CRITICAL** | DataBus tools — unlimited |
| `app/routers/contract_analyzer.py` | **HIGH** | Contract analysis — expensive |
| `app/routers/address_profiler.py` | **HIGH** | Wallet profiling — expensive |
| `app/routers/honeypot_map.py` | **MEDIUM** | Honeypot map visualization |
| `app/routers/bulletin.py` | **MEDIUM** | Bulletin board |
| `app/routers/news.py` | **MEDIUM** | News endpoints |
| `app/routers/death_clock.py` | **LOW** | Death clock feature |
| `app/routers/ai_stream.py` | **MEDIUM** | AI streaming |
| `app/routers/scam_school/` (4 files) | **MEDIUM** | Education platform |
| `app/routers/jwks.py` | **MEDIUM** | JWKS endpoint |

**Count: ~25+ files missing rate limiting.**

### 4.2 Endpoints Missing Authentication

| Endpoint | File | Priority | Notes |
|----------|------|----------|-------|
| `POST /api/v1/rag/search` | `app/api/v1/rag/search.py:36` | **CRITICAL** | RAG search — expensive AI operation, no auth |
| `POST /api/v1/rag/ingest` | `app/api/v1/rag/search.py:45` | **CRITICAL** | Data ingestion — no auth |
| `POST /api/v1/rag/feedback` | `app/api/v1/rag/search.py:54` | **MEDIUM** | Feedback ingestion — no auth |
| `GET /api/v1/rag/stats` | `app/api/v1/rag/search.py:68` | **MEDIUM** | Stats — no auth |
| `POST /api/v1/rag/bulk-ingest` | `app/api/v1/rag/search.py:74` | **CRITICAL** | Bulk ingestion — no auth |
| `GET /api/v1/catalog/stats` | `app/api/v1/catalog/router.py:51` | **MEDIUM** | Catalog — no auth |
| `GET /api/v1/catalog/probe` | `app/api/v1/catalog/router.py:57` | **MEDIUM** | Probe — no auth |
| `GET /api/v1/catalog/tokens/{chain}/{address}` | `app/api/v1/catalog/router.py:64` | **MEDIUM** | Token lookup — no auth |
| `POST /api/v1/catalog/entities/resolve` | `app/api/v1/catalog/router.py:119` | **MEDIUM** | Entity resolution — no auth |
| `POST /api/v1/mcp` | `app/api/v1/mcp/router.py:50` | **CRITICAL** | MCP tool execution — no auth (TODO in mcp/server.py confirms this) |
| `POST /api/v1/mcp/call/{tool_id}` | `app/api/v1/mcp/router.py:167` | **CRITICAL** | MCP tool call — no auth |
| `GET /api/v1/mcp/tools` | `app/api/v1/mcp/router.py:125` | **MEDIUM** | MCP tool listing — no auth |

### 4.3 Other Security Gaps

| Gap | Evidence | Priority |
|-----|----------|----------|
| MCP server exposed without API key check | `app/mcp/server.py:588` — "TODO: M3 moat TIER 2 - add API key check" | **CRITICAL** |
| Public API surface (7 endpoints) entirely 501 | `app/api/v1/public/*` — stubs that should either work or be removed | **MEDIUM** |
| Sync HTTP may leak timing info | 14 files with sync httpx in async handlers — possible timing side-channels | **LOW** |
| Cross-encoder reranker not pinned | `app/cross_encoder_reranker.py:148` — "TODO: pin to specific revision SHA for supply-chain integrity" | **MEDIUM** |
| No CSP for admin extensions | dormant router has no explicit content security policy | **LOW** |

---

## 5. DOCUMENTATION GAPS

### 5.1 Missing or Stale READMEs / Docs

| Doc | Status | Priority | Notes |
|-----|--------|----------|-------|
| `docs/` | 23 files — fairly comprehensive | **OK** | ADRs, API ref, deployment, FAQ, etc. |
| `ARCHITECTURE.md` | Exists | **OK** | Reviewed — current |
| `RMI_SYSTEM_MAP.md` | Exists | **OK** | High-level system map |
| `AUDIT-2026-Q3.md` | Exists (33KB) | **OK** | Detailed audit from July 2026 |
| `STATUS.md` | Exists | **OK** | 3769 bytes, current as of Jul 13 |
| `ROADMAP.md` | Exists | **OK** | Brief |
| `TODO.md` | Exists (870 bytes) | **OK** | Currently minimal |
| **`CHANGELOG.md`** | Stale | **MEDIUM** | Check if it's up-to-date |

**No critical documentation gaps found** — the docs directory is well-maintained. Main gap is that the existing docs describe a system state that may not match the mounted reality (AUDIT-2026-Q3.md is the best reference).

### 5.2 Missing Module-level Docstrings

A random sample of top-level modules without meaningful docstrings:

| File | Priority | Notes |
|------|----------|-------|
| `app/sosana_crm.py` | **LOW** | No docstring |
| `app/scan_rate_limiter.py` | **LOW** | Has brief comment but no formal docstring |
| `app/confidence.py` | **LOW** | No docstring |
| `app/sweep_all.py` | **LOW** | No docstring |
| `app/sweep_now.py` | **LOW** | No docstring |
| `app/brand_marketing_generator.py` | **LOW** | No docstring |
| `app/bulk_marketing_generator.py` | **LOW** | No docstring |
| `app/marketing_graphics_generator.py` | **LOW** | No docstring |

---

## 6. DEPLOYMENT GAPS

### 6.1 Docker Compose Service Coverage

Services in Docker Compose | Present? | Notes
--- | --- | ---
rmi-redis | ✅ | Healthy
rmi-postgres | ✅ | Healthy
rmi-clickhouse | ✅ | Healthy
rmi-neo4j | ✅ | Healthy
rmi-qdrant | ✅ | Profile: `full` only — not started by default
rmi-minio | ✅ | Healthy
rmi-redpanda | ✅ | Healthy
rmi-backend | ✅ | Healthy
rmi-worker | ✅ | Healthy (read-only rootfs, caps dropped)
rmi-prometheus | ✅ | Present (no explicit config files found: `docker/prometheus*` empty)
rmi-grafana | ✅ | Present (no explicit config files found: `docker/grafana*` empty)

**Missing from Compose:**

| Service | Priority | Notes |
|---------|----------|-------|
| **Qdrant** is profile-only | **MEDIUM** | Not started with default `docker compose up` — RAG fails if `full` profile not used |
| **Prometheus config** | **MEDIUM** | `docker/prometheus*` glob returns no results — no prometheus.yml shipped |
| **Grafana dashboards** | **MEDIUM** | No pre-configured dashboards shipped |

### 6.2 Environment Variable Documentation Gap

**.env.example:** 169 lines, 85 documented keys  
**.env (actual):** 21 lines  

**CRITICAL missing env vars from .env (64 not set):**

```
LOG_LEVEL, BACKEND_URL, FRONTEND_URL, DATABASE_URL, ALEMBIC_DATABASE_URL,
REDIS_URL, REDIS_PASSWORD, REDIS_CACHE_URL, CLICKHOUSE_PASSWORD, JWT_SECRET,
JWT_ALGORITHM, JWT_PRIVATE_KEY, JWT_PUBLIC_KEY, JWT_EXPIRE_MINUTES,
RMI_AUTH_TOKEN, RMI_INTERNAL_KEY, DEV_BYPASS_AUTH, TOTP_ISSUER,
CORS_ORIGINS, RATE_LIMIT_PER_MINUTE, RATE_LIMITER_REDIS_DB, RATE_LIMIT_SALT,
OPENROUTER_API_KEY, HUGGINGFACE_TOKEN, LITELLM_API_BASE, DEEPSEEK_API_KEY,
OLLAMA_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI,
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST, SENTRY_DSN,
GLITCHTIP_DSN, OTEL_ENABLED, SENTRY_TRACES_SAMPLE_RATE, COINGECKO_API_KEY,
ETHERSCAN_API_KEY, BIRDEYE_API_KEY
```

**Category: CRITICAL** — 64 of 85 documented env vars are missing from `.env`. This breaks all integrations that require these keys.

### 6.3 Startup Dependencies

| Dependency | Status | Priority |
|------------|--------|----------|
| PostgreSQL migrations via Alembic | 2 migration files exist | **OK** |
| Alembic URL override in env.py | Documented | **OK** |
| Qdrant service is profile-only | **MEDIUM** | Startup order: Qdrant not available in default profile |
| Neo4j health check uses cypher-shell | Works | **OK** |
| Worker health check is a no-op | `python -c "import os,sys; sys.exit(0) if os.path.exists('/proc/self/status') else sys.exit(1)"` | **LOW** | Doesn't actually verify worker readiness |

---

## 7. MONITORING GAPS

### 7.1 Service Monitoring Coverage

| Service | Monitored? | Priority | Notes |
|---------|-----------|----------|-------|
| rmi-redis | Health check only | **LOW** | No metrics collection visible |
| rmi-postgres | Health check only | **LOW** | No query performance monitoring |
| rmi-clickhouse | Health check only | **LOW** | No query analytics monitoring |
| rmi-neo4j | Health check only | **LOW** | No graph query monitoring |
| rmi-qdrant | Profile-only, no monitoring | **MEDIUM** | Not always running |
| rmi-backend | Prometheus target | **MEDIUM** | Prometheus.yml not shipped in repo |
| rmi-worker | Health check is a no-op | **MEDIUM** | Doesn't verify actual processing capability |
| x402 payment system | **NOT MONITORED** | **CRITICAL** | No payment success/failure metrics |
| Scanner pipeline | **NOT MONITORED** | **HIGH** | No scan queue depth, latency, or error rate |
| Webhook delivery | **NOT MONITORED** | **HIGH** | 501 — no delivery at all |
| Alert system | **NOT MONITORED** | **HIGH** | 501 — no alerts at all |
| DataBus | **NOT MONITORED** | **MEDIUM** | No provider health or latency metrics |

### 7.2 Missing Metrics

| Metric | Priority | Notes |
|--------|----------|-------|
| x402 payment success/failure rate | **CRITICAL** | Revenue tracking |
| x402 payment USD volume | **CRITICAL** | Revenue tracking |
| Request rate by endpoint | **HIGH** | No per-endpoint rate tracking |
| Request latency by endpoint (p50/p95/p99) | **HIGH** | No OpenTelemetry traces |
| Scan request queue depth | **HIGH** | Scanner pipeline capacity |
| Scan processing latency | **HIGH** | End-to-end scan time |
| RAG query latency | **MEDIUM** | RAG search performance |
| External API call success rate | **MEDIUM** | Per-provider reliability |
| External API call latency | **MEDIUM** | Per-provider latency |
| Cache hit ratio | **MEDIUM** | Cache effectiveness |
| Database connection pool usage | **LOW** | Connection exhaustion risk |
| Error rate by exception type | **LOW** | Error budgets |

### 7.3 Observability Gaps

| Component | Status | Priority |
|-----------|--------|----------|
| OpenTelemetry | Configured but env vars not set | **HIGH** | `OTEL_ENABLED`, `SENTRY_DSN`, `GLITCHTIP_DSN` all missing from .env |
| Langfuse tracing | Env vars not set | **HIGH** | `LANGFUSE_*` keys missing |
| Sentry error tracking | DSN not configured | **HIGH** | `SENTRY_DSN` missing from .env |
| GlitchTip | Test endpoint exists, webhook 501 | **MEDIUM** | Not wired |
| Prometheus metrics endpoint | Not verified | **MEDIUM** | Need to check if /metrics exists |
| Structured logging | Present (get_logger) | **OK** | Logging infrastructure exists |
| Health check endpoints | Present | **OK** | Multiple health check endpoints |

---

## 8. CROSS-CUTTING GAPS

| Gap | Category | Priority | Notes |
|-----|----------|----------|-------|
| `app/domains/databus/providers/impl/` directory is **empty** | Functionality | **HIGH** | Provider implementations not written, only registry/categories exist |
| `app/protection_router.py.broken` | Deployment | **LOW** | A `.broken` file left in tree — should be removed or fixed |
| `app/routers/x402_docs_v2.py` uses `requests.post` (sync) | Integration | **MEDIUM** | Sync HTTP in route handler |
| `app/routers/bulletin.py` uses sync httpx | Integration | **MEDIUM** | Blocks event loop |
| `app/middleware/validation.py` has no rate limiting | Security | **MEDIUM** | Validation middleware exists but doesn't rate-limit |
| `app/middleware/cost_tracking.py` has pass-only methods | Functionality | **MEDIUM** | Cost tracking middleware is a no-op |
| `wallet_memory/` has no dedicated tests | Testing | **HIGH** | Core wallet memory domain — 20+ files, 0 tests |
| `app/domains/scanner/rugmaps/engine.py` (893 lines) has no tests | Testing | **HIGH** | Core product engine untested |
| `app/domains/billing/` has no tests | Testing | **CRITICAL** | Revenue pipeline untested |
| `app/routers/x402_middleware.py` (896 lines) no tests | Testing | **CRITICAL** | Payment middleware untested |

---

## 9. PRIORITY-ORDERED ACTION PLAN

### CRITICAL (Fix Immediately)

| # | Gap | Category | Effort Est. |
|---|-----|----------|-------------|
| 1 | x402 payment recording is a logging stub — no revenue | Functionality | 3-5 days |
| 2 | MCP server has no auth — public tool execution | Security | 1-2 days |
| 3 | 64 env vars missing from .env — system cannot start | Deployment | 1 day |
| 4 | Payment/billing middleware has no tests (896 + 881 + 860 lines) | Testing | 2-3 days |
| 5 | Public API endpoints are all 501 — external users blocked | Functionality | 1-2 days or remove |
| 6 | No rate limiting on revenue-critical x402 endpoints | Security | 1 day |
| 7 | RAG endpoints have no auth — unlimited AI compute | Security | 0.5 day |
| 8 | MCP endpoint has no auth | Security | 0.5 day |

### HIGH (This Sprint)

| # | Gap | Category | Effort Est. |
|---|-----|----------|-------------|
| 9 | All alert/webhook endpoints are 501 | Functionality | 2-3 days |
| 10 | Labels pipeline not wired to any source | Integration | 2-3 days |
| 11 | Databus provider implementations empty | Functionality | 3-5 days |
| 12 | 14 files use sync HTTP in async context — event loop blocking | Integration | 2-3 days |
| 13 | Entire labels domain (14 files) has 0 tests | Testing | 2-3 days |
| 14 | Entire x402 domain (12 files) has 0 tests | Testing | 2-3 days |
| 15 | Admin extensions router (907 lines, 25 routes) dormant | Functionality | 1 day to remove or activate |
| 16 | Contract analyzer + address profiler lack rate limiting | Security | 0.5 day |
| 17 | Wallet memory domain has no tests (20+ files) | Testing | 2-3 days |
| 18 | RugMaps engine (893 lines) has no tests | Testing | 2-3 days |
| 19 | Flash loan detector not wired to price_consensus | Integration | 1 day |
| 20 | OpenTelemetry/Sentry/GlitchTip env vars not set | Monitoring | 0.5 day |

### MEDIUM (Next 2-3 Sprints)

| # | Gap | Category |
|---|-----|----------|
| 21 | Qdrant profile-only — not started by default | Deployment |
| 22 | Prometheus/Grafana configs not shipped in repo | Deployment |
| 23 | Chainbase API key not configured | Integration |
| 24 | Cross-encoder reranker not pinned to SHA | Security |
| 25 | Scam school content module (894 lines) no tests | Testing |
| 26 | Bulletin board (904 lines) no tests | Testing |
| 27 | News intelligence (858 lines) no tests | Testing |
| 28 | DB client (854 lines) no tests | Testing |
| 29 | Data fusion engine (847 lines) no tests | Testing |
| 30 | RAG pipeline only has manual tests | Testing |
| 31 | Cost tracking middleware is a no-op | Functionality |
| 32 | Prediction market domain (12 files) has 0 tests | Testing |
| 33 | Token domain (8 files) has 0 tests | Testing |
| 34 | Airdrop domain (6 files) has 0 tests | Testing |

### LOW (Technical Debt / Backlog)

| # | Gap |
|---|-----|
| 35 | `protection_router.py.broken` left in tree — remove |
| 36 | Scam school legacy path deprecated — clean up |
| 37 | Module-level docstrings missing for ~20 files |
| 38 | Worker health check is a no-op |
| 39 | Sync HTTP timing side-channels |
| 40 | OLI label source — wire up when endpoint confirmed |

---

## Key Statistics Summary

| Metric | Value |
|--------|-------|
| **Total Python files** | 988 |
| **Router files** | 115 |
| **Test files** | 131 (13% coverage) |
| **Untested domains** | 9 |
| **Stubbed/501 endpoints** | 11 |
| **Dormant/unmounted routers** | 4 |
| **Sync HTTP in async context** | 14 files |
| **Routers missing rate limiting** | ~25+ |
| **Endpoints missing auth** | ~12 |
| **TODO/FIXME markers** | 24 |
| **CRITICAL gaps** | 8 |
| **HIGH gaps** | 12 |
| **MEDIUM gaps** | 14 |
| **LOW gaps** | 6 |
| **Env vars not set** | 64 of 85 documented |

---

*Generated by automated audit — 2026-07-24. Run `make audit-gaps` to refresh or use `scripts/rmi-status.sh` for live health.*
