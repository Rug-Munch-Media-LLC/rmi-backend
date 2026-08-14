# RugBrain + RugMunchBot — Standalone OSS Extraction Plan

**Status:** DRAFT · READ-ONLY planning (no code changed) · 2026-08-14
**Owner:** RMI Engineering
**Scope:** `app/rag/` (RugBrain RAG stack), `app/domains/telegram/rugmunchbot/` (RugMunchBot),
`app/routers/chat/` (chat HTTP API), `app/content_platform.py` (assessment only)
**Companion docs:** `docs/RUGBRAIN-EXTRACTION.md` (RugBrain-only draft), `docs/RUGBRAIN.md` (canonical),
`docs/BOT-IMPROVEMENT-PLAN.md` (bot roadmap), `app/rag/QDRANT-CONSOLIDATION.md`

> This plan supersedes/extends `docs/RUGBRAIN-EXTRACTION.md` by adding the RugMunchBot
> boundary, the bot→RugBrain dependency model, and the shared migration/rename checklist.
> All paths are relative to the private monorepo; anything marked `[INTERNAL]` in the
> source must be replaced with a generic placeholder before publishing.

---

## 0. Source-of-truth read map (what the boundary is based on)

| Area | Modules read | Verdict |
|---|---|---|
| RAG core | `app/rag/{config,qdrant_store,service,engine,retrieval,rerank,embeddings,compat}.py` | **Mostly self-contained** — the engine depends on `app.config` (settings), `app.core.logging`, and other `app.rag.*` modules only. |
| RAG satellite | `app/rag/{crawler,_crawler_constants,_crawler_helpers,ingestion,dedup,embedder,chunking}.py` | **Portable** — crawler sources are public RSS/feeds. |
| RAG coupled | `app/rag/{tools,graph,pryscraper_ingest,intelligence,scan_memory}.py` | **Private-coupled** — import `app.domains.scanner`, `app.services.web3_chat.balances`, `app.services.pryscraper_client`, `app.knowledge_graph.graph`, `app.llm_config`, `app.crypto_embeddings`. |
| Bot product | `app/domains/telegram/rugmunchbot/{bot,handlers/,commands/,formatters/,keyboards,scanner,db,states,flood_control,anti_abuse,chat_ai}` | **OSS product** — Telegram UX, tool-use chat, formatters, local SQLite. |
| Bot private | `app/domains/telegram/rugmunchbot/config.py` (tokens, tiers, channels, fee wallets, DEX ref codes) | **Strip to env/config** — see §2. |
| Chat API | `app/routers/chat/` (`__init__.py`, `_messages.py`, `_prompts.py`) + `app/domains/chat_tools.py` | **Portable core, private glue** — LLM router (`app.ai_router`), auth (`app.auth`), web search (SearXNG) are injectable. |
| Content platform | `app/content_platform.py` | **Stays private** — Ghost publishing + branded briefing/newsletter pipelines. Not part of either OSS package. |

---

## 1. RugBrain — OSS package boundary

### 1.1 What ships as the pure RAG engine

| Module | Role | Boundary |
|---|---|---|
| `qdrant_store.py` | Unified hybrid dense+sparse store (named vectors, RRF k=60, uuid5 idempotent upserts, fail-open) | ✅ **PURE** — only needs Qdrant URL + embedding dim from env. |
| `embeddings.py` / `embedder.py` | bge-m3 embedding via Infinity (`/embeddings`), cache, backend selection | ✅ **PURE** — HTTP to `INFINITY_URL`; embedder backend selected by env. |
| `rerank.py` | Cross-encoder rerank via Infinity `/rerank` (bge-reranker-v2-m3), fail-open | ✅ **PURE** — HTTP only. |
| `chunking.py` | Char-level chunking, content hash, dedup keys | ✅ **PURE** — no app imports. |
| `dedup.py` | SHA-256 dedup, spam-repetition gate | ✅ **PURE**. |
| `ingestion.py` + `ingestion_models.py` + `processor.py` | BatchIngestor, QualityFilter, RateLimiter, scheduler | ✅ **PURE** — Redis is a dependency (see §1.4). |
| `retrieval.py` | HybridRetriever, ContextBuilder, RelevanceScorer, QueryExpander | ⚠️ **PORTABLE** — one lazy import of `app.knowledge_graph.graph` (entity boost) must become an optional protocol. |
| `engine.py` | Three-pillar search (dense + BM25-lite + metadata filter), RRF fusion, ingest | ⚠️ **PORTABLE** — uses `app.config.settings`; rewrite to local `Settings`. |
| `crawler.py` + `_crawler_constants.py` + `_crawler_helpers.py` | Crawl4AI lifecycle, RSS discovery, quality/freshness gates | ✅ **PURE** — `crawl_sources.json` is public feeds (Cointelegraph, The Block, Peckshield, etc.). `[REVIEW]` `incident_sources` sub-map for any non-public URLs. |
| `models.py`, `quality.py`, `rate_limiter.py`, `evaluation.py`, `feedback.py`, `tracing.py`, `ann_index.py`, `config.py` | Typed API surface, eval gates, OpenInference tracing, legacy numpy ANN fallback, tuning knobs | ✅ **PURE** — tracing must keep the OpenInference span vocabulary but drop any Phoenix endpoint default. |
| `compat.py` | Deprecated `app.rag_service` shims | **LEAVE BEHIND** — it re-exports `app.crypto_embeddings`. Ship a minimal `compat.py` only if OSS consumers need the old dict-shaped API; otherwise delete. |

### 1.2 What stays private (never ships)

| Module / asset | Why private | OSS treatment |
|---|---|---|
| `tools.py` | Imports `app.domains.scanner` (token/wallet scanners) + `app.services.web3_chat.balances` (live balances) | Define `ScannerClient` / `BalancesClient` protocols; ship a `tools_oss.py` demo with stubs, or drop tools from v1. |
| `graph.py` | Uses `app.llm_config` + knowledge-graph extraction | Optional `KGProvider` protocol, default `None`. |
| `pryscraper_ingest.py` | Bridges to the pryscraper service | Stub provider (no-op ingest) behind `CrawlerProvider`. |
| `intelligence.py` / `scan_memory.py` | Scanner-result semantic memory + `app.crypto_embeddings` | Not in OSS v1; keep as private extension modules in the monorepo. |
| `crawl_sources.json` `incident_sources` | May carry non-public incident URLs | Ship the public `news/security/research/onchain` groups; move incident feeds to `RAG_CRAWL_SOURCES_EXTRA` env JSON. |
| Proprietary collection semantics | `scanner`, `wallet`, `news`, `research`, `scams` (live) and legacy `scam_intel`, `deployer_history`, `wallet_labels`, `contract_audit`, `phishing_db` are RugMunch data contracts | Ship **example collection configs** (`config/collections.example.json`) with the same *schema* (payload keys: `content`/`text` + `metadata.*`) but neutral names/contents. |
| Anything importing `app.db`, `app.databus`, `app.core.*` beyond logging | Private persistence + 30+ provider DataBus | Not ported. `app.core.logging` → local `structlog`/stdlib logger adapter. |

### 1.3 Proposed repo structure

```
rugbrain/
├── pyproject.toml            # name="rugbrain", deps: qdrant-client, httpx, redis,
│                             #   pydantic>=2, pydantic-settings, crawl4ai (extra), fastapi (extra)
├── src/rugbrain/
│   ├── __init__.py           # public API re-exports (mirror app/rag/__init__.py)
│   ├── core/                 # pure engine
│   │   ├── chunking.py  dedup.py  models.py  quality.py  evaluation.py  feedback.py
│   │   ├── retrieval.py      # HybridRetriever, ContextBuilder, QueryExpander
│   │   ├── rerank.py         # Infinity cross-encoder client
│   │   └── engine.py         # three-pillar search + ingest facade (RAGService)
│   ├── ingest/
│   │   ├── ingestion.py  processor.py  rate_limiter.py  ingestion_models.py
│   │   ├── crawler.py  _crawler_constants.py  _crawler_helpers.py
│   │   └── sources.json      # public feed groups only
│   ├── storage/
│   │   ├── qdrant_store.py   # QdrantStore (dense+sparse, RRF, uuid5)
│   │   ├── ann_index.py      # optional in-process fallback
│   │   └── cache.py          # Redis embed-cache + dedup-hash adapter (protocol)
│   ├── embed/
│   │   ├── embeddings.py     # Infinity /embeddings client + EmbeddingProvider protocol
│   │   └── embedder.py       # backend selection (infinity | bge-m3 | openrouter)
│   ├── observability/
│   │   └── tracing.py        # OpenInference spans; Phoenix URL via env, off by default
│   ├── providers.py          # Protocol definitions: EmbeddingProvider, Reranker,
│   │                         #   ScannerClient, BalancesClient, NewsProvider, CacheBackend
│   ├── settings.py           # pydantic-settings (env-driven, see §1.5)
│   └── api/                  # optional FastAPI server (extra)
│       ├── server.py         # /search /ingest /stats /feedback (mirror app/routers/rag_v2.py)
│       └── schemas.py
├── cli/
│   ├── __init__.py  main.py  ingest.py  crawler.py  eval.py   # `rugbrain` console entry
├── config/
│   ├── collections.example.json   # neutral collection schemas
│   └── crawl_sources.example.json
├── tests/  # mirror of test_rag_*.py, test_chunking, test_qdrant_store (renamed, §3.3)
├── docs/  AGENTS.md  LICENSE(MIT)  SECURITY.md  CODEOWNERS  .gitleaks.toml  lefthook.yml
```

### 1.4 Public API (target)

```python
from rugbrain import RAGService, init_rag
from rugbrain.core.models import SearchRequest, SearchResponse, SearchHit, IngestRequest, IngestResult, FeedbackRecord
from rugbrain.storage.qdrant_store import QdrantStore, get_qdrant_store, upsert_rag_document
from rugbrain.ingest import BatchIngestor, QualityFilter, get_ingestor, ingest_document

service = RAGService()                       # async facade
resp = await service.search(SearchRequest(query="...", collection="docs", top_k=10))
result = await service.ingest(IngestRequest(collection="docs", content="...", metadata={}))
```

### 1.5 Config via env (pydantic-settings)

| Env var | Default | Purpose |
|---|---|---|
| `RAG_QDRANT_URL` | `http://127.0.0.1:6333` | Vector store endpoint (no auth in OSS default) |
| `RAG_QDRANT_COLLECTION` | `rag_documents` | Collection name; override per deployment |
| `RAG_STORE` | `qdrant` | `qdrant` \| `legacy` (numpy+Redis fallback) |
| `INFINITY_URL` / `EMBEDDER_PRIMARY` | `http://127.0.0.1:8001`, `infinity` | Embedding + rerank backend |
| `REDIS_URL` / `RAG_CACHE_REDIS_URL` | `redis://127.0.0.1:6379/0` | Embed cache + dedup hashes (optional; in-memory fallback) |
| `RAG_CONTEXT_MAX_TOKENS`, `RAG_INGESTION_BATCH_SIZE`, `RAG_CHUNK_SIZE`, `RAG_HYBRID_ALPHA`, `RAG_RRF_K` | see `app/rag/config.py` | All current knobs move verbatim |
| `RAG_COLLECTIONS` | `["docs"]` | Active collections (neutral default) |
| `RAG_CRAWL_SOURCES_EXTRA` | `""` | JSON map of extra crawl sources (replaces private feeds) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (off) | OpenInference tracing |
| `RAG_EVAL_HIT_RATE_MIN`, `RAG_EVAL_FAITHFULNESS_MIN` | 0.5 / 0.25 | CI eval gates |

**Never in OSS:** API keys, token wallets, secret-file paths, `[INTERNAL]` hostnames, gopass
secret paths. All credentials come from the host environment or a secrets manager.

### 1.6 Stubs / mocks shipped for OSS

- `providers.py` protocols + a `stubs/` package: `ScannerClientStub` (returns canned risk
  verdicts), `BalancesClientStub`, `NewsProviderStub` (returns the bundled sample articles).
- `config/collections.example.json` + a `rugbrain seed-demo` CLI command that ingests the
  bundled `examples/data/*.md` (e.g. a fake audit report, a fake post-mortem) so the repo
  is testable offline with Qdrant+Infinity only.
- `tests/` use an in-memory Qdrant (`qdrant-client[local]` with `:memory:` mode) and mocked
  Infinity (`respx`) — no live services in CI.

---

## 2. RugMunchBot — OSS boundary

### 2.1 OSS product surface (ships)

| Area | Modules | Notes |
|---|---|---|
| Telegram entry | `bot.py`, `states.py`, `flood_control.py`, `anti_abuse.py`, `admin_state.py` | `admin_state.py` keeps usage tracking but the owner/admin command *surface* is gated by `is_owner` from config |
| Chat + tool-use | `handlers/chat.py`, `handlers/chat_ai/` (`__init__.py`, `_helpers.py`, `_llm.py`), `ai_chat.py` (deprecated but referenced), `handlers/ask.py`, `handlers/research.py` | The single free-text AI pipeline — calls the chat API (see §2.3) |
| Commands | `commands/` (chart, events, portfolio, simulate, smartmoney, rugmap) | `premium*.py` become config-gated feature flags, not hard tiers |
| Handlers | `handlers/` (scanning, market, price, holders, sentiment, trace, investigate, defi, prediction, watchlist, alerts, webhook, inline, report, groups) | Some handlers call scanner endpoints (see §2.2) |
| Formatters | `formatters/` (`_base.py`, `_sentinel.py`, `_sentinel_helpers.py`, `_wallet.py`) | Pure text/keyboard builders — fully portable |
| Support | `keyboards.py`, `prompts.py`, `scanner.py`, `db.py` (SQLite), `ta_indicators.py`, `daily_context.py`, `services/visuals.py`, `support/logging.py` | Local-only deps |
| Config | `config.py` | **Rewritten** — env-only, no hardcoded secrets (§2.2) |

### 2.2 What is stripped into env/config (never hardcoded)

| Current hardcoded value | OSS replacement |
|---|---|
| Bot token via gopass secret path | `RUGMUNCH_BOT_TOKEN` env (or `TELEGRAM_BOT_TOKEN`) only |
| Backend `X-API-Key` from secret file/gopass | `RMI_BACKEND_URL` + `RMI_API_KEY` env; secret-file lookup removed |
| `OWNER_IDS` default (numeric Telegram ID) | `BOT_OWNER_IDS` env, empty default; admin commands disabled when unset |
| `CHANNELS` numeric IDs (`main/news/alerts/alpha/scans`) | `BOT_CHANNELS` env as JSON (`{"alerts": "-100..."}`), no defaults |
| `TIERS` pricing/Stars packs, `TOP_UP_PACKS`, `BUY_STARS_PACKS`, `SCAN_PACKS` | **Billing stays private** — OSS ships a single `free` tier with env-tunable weekly quotas (`BOT_FREE_SCANS_PER_WEEK`, `BOT_FREE_AI_MSGS_PER_WEEK`); Telegram Stars payments are a plugin behind a `BillingProvider` protocol |
| `JUPITER_FEE_ACCOUNT` / `JUPITER_ULTRA_ACCOUNT` (revenue wallets) | Remove from OSS; `BOT_DEX_REF_*` env overrides for affiliate links |
| `DEX_REF_LINKS` ref codes | Ship templates with `ref=` placeholder; operators set their own codes |
| Branding (website URL, X/Twitter handle, Telegram channel link, bot profile blurb) | `BOT_NAME`, `BOT_WEBSITE_URL`, `BOT_TELEGRAM_CHANNEL`, `BOT_DESCRIPTION` env |
| `SENTINEL_URL` → `.../api/v1/x402-tools/sentinel_scan` | **x402 coupling** — see §2.3; OSS default points at the chat API's `/scan`-style endpoint or a `ScannerProvider` |
| `BACKEND_HOST` docker-compose service name | `RMI_BACKEND_URL` env (default `http://localhost:8000`) |

**Stays entirely private (not in the OSS repo):** `app/content_platform.py` (Ghost
publishing + briefing/newsletter), `app/domains/telegram/{headless,voice_chat,scanner_bridge,adminbot}`,
admin control-panel handlers (`handlers/owner.py` command set), Telegram payment webhook
handlers, and the `data/` bot database seeds.

### 2.3 How RugMunchBot depends on RugBrain

**Primary model — HTTP service (recommended for OSS):**
RugMunchBot is a thin Telegram client that talks to a RugBrain-backed chat HTTP API:

```
Telegram user ⇄ rugmunchbot (python-telegram-bot, stateless)
                     │  POST /api/v1/chat   (RMI_BACKEND_URL)
                     ▼
             rugbrain-api (FastAPI, optional extra)
                     ├── RAGService (three-pillar search + context build)
                     ├── tool-use layer (chat_tools equivalent: price/scan/TA/news executors)
                     └── LLM router (injectable — OpenRouter/Zen/local)
```

- The bot's `chat_ai` pipeline becomes: backend chat API first → local LLM fallback
  (exactly the current tier-1/tier-2/tier-3 order, but the "backend" is now the OSS
  `rugbrain-api` instead of the private monolith).
- The chat API surface is **already extracted** in `app/routers/chat/` (`_messages.py`,
  `_prompts.py` pure helpers + `app/domains/chat_tools.py` intent/executor layer). These
  move to `rugbrain/api/` (or a `rugbrain-chat` sibling package) with three injectable
  seams: `LLMRouter` (replaces `app.ai_router`), `WebSearchProvider` (replaces SearXNG
  import), `AuthProvider` (replaces `app.auth.get_current_user` — OSS default = no-op).
- **x402 / billing seam:** the private backend exposes scan endpoints behind x402
  payment-gating (`/api/v1/x402-tools/*`). The OSS bot must never assume x402. Route all
  privileged data access through a `DataProvider` protocol the bot defines:
  `resolve_with_fallback(...)` in `data_resolver.py` already enforces the
  own-data-first hierarchy — in OSS the "own data" tier is the local RugBrain store, and
  the paid/scan tier is a stub or the operator's own deployment.

**Secondary model — pip dependency (optional):** `rugmunchbot` declares
`rugbrain>=0.1` and imports `rugbrain.core.models` / `rugbrain.storage.qdrant_store`
directly for embed + retrieve, bypassing the HTTP hop. Fine for single-process
self-hosting; the HTTP model stays the default so the bot and RAG can scale separately.

**Versioning:** both packages share a release cadence (see §3.4) so the chat API schema
(`/api/v1/chat` request/response) can be pinned in a `RUGBRAIN_API.md` contract and
versioned independently of the Python packages.

### 2.4 Proposed RugMunchBot repo structure

```
rugmunchbot/
├── pyproject.toml            # deps: python-telegram-bot>=21, httpx, pydantic>=2, rugbrain (optional)
├── src/rugmunchbot/
│   ├── __init__.py           # re-export bot.main + formatters (mirror current __init__)
│   ├── bot.py                # Application build + main (entry)
│   ├── config.py             # pydantic-settings: BOT_TOKEN, BOT_OWNER_IDS, BOT_CHANNELS, RMI_BACKEND_URL...
│   ├── db.py                 # SQLite users/tiers/quota (tiers from config, no billing)
│   ├── states.py  flood_control.py  anti_abuse.py  admin_state.py  ta_indicators.py
│   ├── data_resolver.py      # own-data-first fallback chain (RugBrain → provider stubs → web)
│   ├── scanner.py            # ScanProvider protocol + built-in free scanners (public APIs only)
│   ├── chat/
│   │   ├── pipeline.py       # chat_ai_response equivalent (chat API → local fallback)
│   │   └── tool_use.py       # intent classifier + executors (ported from app/domains/chat_tools.py)
│   ├── commands/             # chart, events, portfolio, simulate, smartmoney, rugmap
│   ├── handlers/             # scanning, market, price, holders, trace, investigate, defi,
│   │                         #   sentiment, prediction, watchlist, alerts, ask, research, report
│   ├── formatters/           # _base, _sentinel, _sentinel_helpers, _wallet (pure)
│   ├── keyboards.py  prompts.py  daily_context.py  services/visuals.py  support/logging.py
│   └── plugins/              # BillingProvider, ScannerProvider, DataProvider protocol stubs
├── tests/                    # port test_bot_*, test_chat_ai_*, test_telegram_transport.py
├── docker/  docs/  AGENTS.md  LICENSE(MIT)  SECURITY.md  .gitleaks.toml  lefthook.yml
```

---

## 3. Migration plan (P0 → P1 → P2)

Shared rule across both packages: **one atomic extraction unit per commit**, and the
monorepo keeps working at every commit (green suite on `main` at all times).

### 3.0 The rename-failure lesson (read first)

The lite-chain-indexer rename (commit `8799ff2f`, "fix: rename fallout tests") broke the
suite because renames were done file-by-file: stale import paths in `app/domains/indexer/`,
`app/cross_chain_tracking/`, scanner query modules, and formatters were fixed only after
CI failed, and lazy/dynamic module maps (`__getattr__`, `importlib.import_module`,
`__all__` re-exports) referenced the old name in places plain grep initially missed.

**Prescribed rename/extraction checklist (run per module, in order):**
1. **Grep every reference**: `rg -n "app\.rag|app\.domains\.telegram\.rugmunchbot" --type py` across the repo — code AND tests AND `pyproject.toml`/`mypy.ini`/`pyrightconfig.json`/`pytest.ini`/CI workflows.
2. **Audit lazy maps first**: every `__init__.py` re-export (`__all__`), `__getattr__` shims, `importlib`/`getattr(module, name)` call sites, string-based router registrations, and `TYPE_CHECKING` imports. These are the silent breakers.
3. **Update in one atomic change**: module rename + import rewrites + `__all__` + lazy maps + test imports + any snapshot/golden files, in a single commit. Never split the rename from its consumers.
4. **Run the full suite** (not just the touched tests): `.venv/bin/python -m pytest` (plus the `rag_eval` marker: `.venv/bin/python -m pytest -m rag_eval`). The monorepo suite includes cross-cutting tests (`test_chat_*`, `test_bot_*`) that import both packages.
5. **Run static gates**: `ruff check`, `mypy --strict`, `gitleaks detect --no-git` (extraction pulls source out of the private repo — verify no secret leaks into the new tree).
6. **Keep a shim one release**: after moving a module, leave a thin re-export shim in the old location for one release cycle, then delete it. This is exactly what `app/rag/compat.py` already does for `rag_service` — reuse that pattern.
7. **Update docs + CI references** in the same commit (this doc, `docs/RUGBRAIN.md`, Makefile, workflows).

### 3.1 P0 — RugBrain extraction (atomic port)

- [ ] Copy the §1.1 pure modules verbatim into `rugbrain/src/rugbrain/`; keep `app.rag.*` import lines working via `sys.modules` alias during transition (or `compat.py` shims).
- [ ] Introduce `rugbrain/settings.py` (pydantic-settings) and replace `from app.config import settings` in the 7 touched modules (`qdrant_store`, `rerank`, `embeddings`, `embedder`, `crawler`, `scan_memory`, `engine`).
- [ ] Replace `app.core.logging` with a local logger adapter (stdlib `logging` + optional structlog).
- [ ] Ship `providers.py` protocols + `stubs/` + `config/collections.example.json` + `seed-demo` CLI.
- [ ] Port `tests/test_rag_*.py`, `tests/test_chat_*` RAG bits, chunking/dedup tests → `rugbrain/tests/`; run both suites green.
- [ ] Add `rugbrain` to the monorepo dev requirements (local path) and shim `app.rag` re-exports; **delete `app/rag_service/`** per QDRANT-CONSOLIDATION.
- **Risk notes (P0):** `retrieval.py`'s lazy `app.knowledge_graph` import must fail open when absent (currently it is lazy — good); `engine.py` reads `settings.qdrant_url` at import time — move to lazy resolution so OSS import without env never crashes; `crawl_sources.json` `incident_sources` must be scrubbed before the repo goes public — run gitleaks + a URL allow-list audit.

### 3.2 P1 — RugMunchBot extraction (atomic port)

- [ ] Rewrite `config.py` as pydantic-settings per §2.2 — **this is the risky change**: `config.py` is imported by ~56 bot modules and every other bot file re-exports from it. Apply §3.0 checklist steps 1–4 on this file alone.
- [ ] Port pure surface (`formatters/`, `keyboards.py`, `prompts.py`, `states.py`, `flood_control.py`, `ta_indicators.py`, `support/logging.py`, `services/visuals.py`) first — zero backend deps.
- [ ] Port `db.py` (SQLite) + `admin_state.py` with billing stripped to `BillingProvider` stub.
- [ ] Port `handlers/` + `commands/` with backend calls routed through `data_resolver.py` + `ScanProvider`/`DataProvider` protocols; default providers use public APIs (DexScreener, CoinGecko, GoPlus, Honeypot.is — all already in the bot's config) and the local RugBrain chat API.
- [ ] Port `chat_ai` pipeline + `tool_use.py` against the OSS chat API contract; local LLM fallback via `LLMRouter` (env `BOT_LLM_API_BASE` / `BOT_LLM_API_KEY`).
- [ ] Replace `app.core.*` imports in `admin_state.py`, `alert_worker.py`, `daily_context.py`, `formatters/_base.py` (redis, shared_utils) with local adapters.
- [ ] Port `tests/test_bot_*.py`, `test_chat_ai_*.py`, `test_telegram_transport.py`; shim the old bot package path in the monorepo for one release.
- **Risk notes (P1):** `handlers/scanning.py` (58KB, the biggest handler) embeds x402/backend endpoints and paywall text — split it into `scan_flow.py` (portable) + `scan_backend.py` (private seam). The `formatters/_sentinel.py` (47KB) is pure but huge — split into per-module formatters during porting (do NOT rename-in-place). `data_resolver.py` currently hardcodes the private backend URL/headers in `config.py` — the protocol must take a `DataProvider` at construction time so tests can inject stubs. Bot tests that assert branded strings (`Rug Munch Intelligence`, channel links) must be parametrized over `BOT_*` env.

### 3.3 P2 — chat API + decommission

- [ ] Extract `app/routers/chat/` helpers (`_messages.py`, `_prompts.py`) + `app/domains/chat_tools.py` into `rugbrain/api/` with the three injectable seams (LLMRouter, WebSearchProvider, AuthProvider).
- [ ] Add a FastAPI `rugbrain-api` entrypoint (`uvicorn rugbrain.api.server:app`) and a `RUGBRAIN_API.md` contract (request/response for `/api/v1/chat`, `/search`, `/ingest`, SSE stream).
- [ ] Point the private bot at the private backend chat route (unchanged) and the OSS bot at `rugbrain-api`; run a shadow traffic comparison for one release.
- [ ] Delete shims: `app.rag` re-exports → thin `import rugbrain` aliases → removed; bot shim removed; `app/rag_service/` already gone.
- [ ] Repo hygiene: create both repos with the fleet standard files, protect `main`, mirror to remotes, tag `v0.1.0`, add to `push-all-repos.sh` / fleet compliance.
- **Risk notes (P2):** the chat router imports `app.ai_router` (multi-provider rotation) — the LLMRouter seam must preserve the same provider-order semantics or answers change. SSE streaming (`/chat/stream`) is the most coupled endpoint (Redis history, auth, rate-limit middleware) — extract it last, or document it as private-only for v1. Cross-cutting tests `test_chat_endpoints.py`, `test_chat_guardrails.py`, `test_web3_chat.py` exercise both packages — keep them in the monorepo until shims are deleted.

### 3.4 Release cadence

Both packages cut together on the same tag with a `RUGBRAIN_API.md` contract pin.
Version bumps: `rugbrain` 0.x moves the vector schema / collection config; `rugmunchbot`
0.x moves the Telegram UX. A breaking chat-API change bumps both majors in lockstep.

---

## 4. Principles — "no god code" + modern AI/Web3-forward framework

1. **No god modules.** The extraction is also a de-godding pass: `config.py` (696 lines of
   tiers+channels+tokens), `handlers/scanning.py` (58KB), `formatters/_sentinel.py` (47KB),
   `engine.py` (906 lines) are split along the seams above during porting — never renamed
   in place (see §3.0). Every module has one job, typed inputs/outputs, and no hidden
   module-level side effects at import time (lazy clients everywhere, as `qdrant_store.py`
   already does).
2. **Async-first, typed, pydantic v2.** All I/O is `async` (FastAPI/httpx/AsyncQdrantClient);
   every boundary crosses with pydantic v2 models (`SearchRequest`/`SearchResponse` already
   exist in `app/rag/models.py` — they become the wire contract). `mypy --strict` + `ruff`
   are CI gates from day one.
3. **Protocols over concrete imports.** `EmbeddingProvider`, `Reranker`, `ScannerClient`,
   `BalancesClient`, `NewsProvider`, `CacheBackend`, `LLMRouter`, `WebSearchProvider`,
   `AuthProvider`, `BillingProvider` — the private monolith implements the real providers;
   OSS ships stubs. This is the single most important decoupling tool and it is what makes
   the private repo able to consume the OSS packages.
4. **Env-driven configuration.** pydantic-settings everywhere; no constants for credentials,
   channel IDs, pricing, or endpoints. Secrets come from env or a secrets manager only.
5. **Fail-open everywhere.** Every external call (Qdrant, Infinity, crawler, LLM, scanners)
   degrades to a documented fallback instead of raising — the engine already does this; the
   bot's `data_resolver` own-data-first chain is the same philosophy.
6. **Observable by default.** OpenInference spans in `rugbrain` (Phoenix/OTLP via env),
   structured logging in both packages, and the RAG eval gates (`RAG_EVAL_*`) stay in CI.
7. **Web3-forward.** Collection payload schemas carry `chain`/`address`/`source` metadata
   conventions (already used by `qdrant_store._point_to_hit`), the sparse-vector tokenizer
   stays byte-identical on ingest and query sides, and provider seams are the place where
   future on-chain data sources plug in without touching the core engine.

---

## Appendix A — [REVIEW] flags (riskiest coupling points)

| # | Flag | Why it's risky | Mitigation |
|---|---|---|---|
| 1 | `config.py` in the bot | Imported by ~56 modules + re-exported via package `__init__`; contains gopass paths, secret-file paths, owner IDs, channel IDs, fee wallets, pricing | Rewrite first, in one atomic commit (§3.2 step 1); audit `__all__` re-exports with §3.0 step 2 before touching anything else |
| 2 | `handlers/scanning.py` + `formatters/_sentinel.py` | Deepest x402/backend/paywall entanglement; 58KB + 47KB god files | Split along scan-flow vs backend-seam lines during porting; never rename in place |
| 3 | `chat_ai` pipeline → `/api/v1/chat` | Bot's tier-1 synthesis path depends on the private chat API, which imports `app.ai_router` (provider rotation), `app.auth`, `app.security.chat_guardrails`, SearXNG | The OSS chat API + three injectable seams (§3.3) must be extracted and contract-pinned before the bot ships |
| 4 | `data_resolver.py` own-data-first chain | Tier-1 "own data" is the private lite-indexer + 30+ DataBus providers; tier ordering is a market advantage | Make the chain fully provider-injected; OSS default = local RugBrain store → public APIs → web search |
| 5 | `crawl_sources.json` `incident_sources` | May contain non-public incident URLs | Scrub + gitleaks + URL allow-list audit before first public tag |
| 6 | `retrieval.py` lazy `app.knowledge_graph` import + `engine.py` import-time `settings` reads | Import-order landmines: importing the OSS package in a fresh env must not crash or probe services | Lazy-resolve all settings/endpoints; add an import-smoke test to CI |
| 7 | Naming collision with existing `rmi-rag` repo | An existing repo already claims the "RugBrain" name with a different codebase (`src/rmi_rag/`) | `[REVIEW]` decide supersede vs. merge vs. rename before creating the new repo (already flagged in `docs/RUGBRAIN-EXTRACTION.md`) |
| 8 | Content platform (`content_platform.py`) | Ghost publishing + branded briefing/newsletter stays private; it imports `app.rag.compat.three_pillar_search` | Replace with the `rugbrain` API call in the private repo before `app.rag` shims are deleted (P2) |
| 9 | DEX referral + fee wallets | Revenue-generating config (affiliate codes, Jupiter fee accounts) must not leak | Strip to `BOT_DEX_REF_*` env; gitleaks rule for wallet patterns on both repos |
| 10 | Cross-cutting tests (`test_chat_*`, `test_bot_*`, `test_web3_chat.py`) | Import both packages; deleting shims prematurely breaks the monorepo suite | Keep tests + shims until P2 completes; the suite is the rename-checklist canary (§3.0 step 4) |
