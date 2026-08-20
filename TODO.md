# rmi-backend — Production TODO
**Last audit:** 2026-08-20 (full system audit) · **Status:** Core functional, auth/x402 broken, RAG embedder chain needs migration · **Grade:** D+ (compliance 55%)

## 🔴 BLOCKING (Security & Functionality)

- [ ] **CRITICAL: Rotate RMI_AUTH_TOKEN** — Currently `VITE_API_KEY` in public frontend bundle. Every protected endpoint is public. Generate new token, update all consumers (frontend, bot, walletpress, MCP fleet). `app/core/auth.py:134`
- [ ] **CRITICAL: Remove ClickHouse password from source** — Hardcoded in `app/databus/provider_implementations/passthrough.py:173,280`, `app/domains/scanners/cross_chain_trace/queries.py:23`, `scripts/replay_deployer_events.py`. Move to secrets/env. Rotate password.
- [ ] **CRITICAL: Mount auth domain OR migrate to better-auth** — `app/domains/auth` is NOT in `mount.py` ROUTER_MODULES. All `/api/v1/auth/login|register|wallet|profile|2fa` return 404. Frontend can't log in.
- [ ] **CRITICAL: Delete DataBus consumer_type bypass** — `app/databus/access_control.py:633-638` honors client-declared `consumer_type=admin`. Remove entirely, derive from verified JWT.
- [ ] **CRITICAL: Gate DataBus webhook registration** — `POST /api/v1/databus/webhooks/setup/*` is under public prefix. Attacker can register webhooks using our Helius key. Add auth.
- [ ] **CRITICAL: Fix webhook signature fail-open** — `app/databus/webhooks.py:198-199`: `if not secret or not signature: return True`. Fail closed.
- [ ] **CRITICAL: Fix x402 payment verification** — Base verifier doesn't check amount/token/payer. Solana verifier checks only tx existence. Add proper Transfer event parsing.
- [ ] **CRITICAL: Set x402 production config** — `X402_PAYMENT_ADDRESS`, `X402_HMAC_KEY`, `X402_PAYMENT_CHAIN` all MISSING in container. Payments go to literal placeholder.
- [ ] **CRITICAL: Load DataBus provider keys** — 25/26 API keys missing in container (only HELIUS_API_KEY set). Arkham, Moralis, Alchemy, Bitquery, Dune, Nansen all return empty. Load from vault/env.
- [ ] **CRITICAL: Migrate RAG embedder from Infinity to llama.cpp Qwen3** — `.env.example` still references `INFINITY_URL=http://localhost:8001`. rmi-rag uses Qwen3-Embedding-4B on :8004. Backend must switch to `QWEN_EMBED_URL=http://172.22.0.1:8004/v1` and add instruction prefix for queries. `app/rag/embeddings.py` backend → llama.cpp :8004.
- [ ] **CRITICAL: Fix host cron Redis auth (news/RAG ingest)** — `/srv/secrets/backend.env` exports `REDIS_PASSWORD` but `app/core/redis.py` reads `REDIS_URL` (default no-auth `redis://localhost:6379/0`), so host-side `rmi-news-ingest.sh` step 2 fails "HELLO must be called with the client already authenticated". Container unaffected (uses REDIS_HOST/PORT/PASSWORD directly). Add `REDIS_URL` to cron env or add URL-building validator in `app/core/redis.py`.

## 🟠 HIGH (Data & Reliability)

- [ ] **Fix news router Query bug** — `app/routers/news.py`: `sentiment` default is `Query(None)` causing `AttributeError` on `.lower()`. Change internal calls to pass `None` not `Query(None)`. Breaks `/sources`, `/stats`, `/headlines`.
- [ ] **Fix /api/v1/rugcharts/intel always 500** — `app/routers/rugcharts.py:734`: `f["factor"]` iterates over string list. `_compute_rug_score` returns `list[str]`, comprehension expects dicts.
- [ ] **Mount or delete the 77-endpoint x402-tools router** — `app/domains/billing/x402/router.py` is "intentionally NOT wired". Frontend docs reference these endpoints. Either mount (with auth) or remove docs.
- [ ] **Fix Temporal worker** — Polling `degenfeed-postiz-temporal:7233` (wrong host, wrong app). No rmi Temporal server exists. Either stand up dedicated server or kill worker. `app/temporal/worker.py`
- [ ] **Fix /api/v1/openapi.json 500** — Router mount failure breaks docs. Investigate conflict in `mount.py`.
- [ ] **Consolidate 4 chain registries** — `app/chain_registry/`, top-level `app/chain_registry.py`, `app/wallets/chain_registry.py`, `app/domains/databus/_generated/chains/*`. Pick one.
- [ ] **Fix cross-tier cache poisoning** — `app/databus/services/client.py:349` mutates cached dict in place. Cache key ignores consumer_type. First caller's tier sticks for all.
- [ ] **Fix WS streaming auth** — `/api/v1/databus/ws/{channel}` returns 403 even with valid key. Browsers can't set WS headers. Add token query param.
- [ ] **Parameterize ClickHouse queries** — `app/databus/provider_implementations/passthrough.py:179-184` interpolates user address into SQL. SQL injection risk.
- [ ] **Remove hardcoded Redis password** — `databus_warm_cron.py` uses `REDIS_PASSWORD="test"`.
- [ ] **Fix 2FA pre-auth token bypass** — `app/domains/auth/routes_email.py:108-122` issues pre-auth JWT that works as full session. Enforce `pre_auth` claim.
- [ ] **Fix CSRF chicken-and-egg** — CSRF cookie set after login, but middleware requires it on login POST.
- [ ] **Make wallet auth require signature** — `wallet.py` creates user for any address with zero proof of ownership (latent, domain unmounted).
- [ ] **Fix internal hostname in test fixtures** — `test_secret_host.txt` contains `[INTERNAL]` in tracked file.

## 🟡 MEDIUM (Code Quality)

- [ ] **Consolidate duplicate lifespans** — `app/lifespan.py` (171 lines, used) vs `app/core/lifespan.py` (117 lines, dead). Delete dead copy.
- [ ] **Consolidate duplicate error handlers** — `app/error_handlers.py` vs `app/core/errors.py`.
- [ ] **Consolidate duplicate chain registries** (see HIGH).
- [ ] **Reduce `except: pass` count** (988 occurrences).
- [ ] **Consolidate `print()` calls** (569) to structured logging.
- [ ] **Update ARCHITECTURE.md domain list** — Shows non-existent domains, misses 15 real ones.
- [ ] **Update SECURITY.md** (version table meaningless).
- [ ] **Add per-user auth** (replace shared `RMI_AUTH_TOKEN`).
- [ ] **Wire ELITE scanner tier stubs or remove from UI** — Nansen, Blowfish, Dune, Arkham, Birdeye all return "unavailable".
- [ ] **Schedule deployer_tracker.py or integrate into lite-indexer**.

## 🟢 LOW (Polish)

- [ ] Commit generated `openapi.json` (not empty stub).
- [ ] Remove `.broken` files from repo (`app/protection_router.py.broken`).
- [ ] Schedule deployer_tracker.py or integrate into lite-indexer.

## Tests

- [ ] Green the CI pipeline (2,711 ruff / 7,768 mypy errors).
- [ ] Add x402 payment verification tests (0 exist).
- [ ] Add webhook auth tests.
- [ ] Add DataBus access control tests.
- [ ] Fix `pytest -m "not integration"` — 370 tests deselected, need proper marker registration.