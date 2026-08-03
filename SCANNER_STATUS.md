# RugMunchScan — Status & Capabilities

> Living document. Last updated: 2026-08-03

## Architecture

RugMunchScan has 4 core scanner engines, 7 capability engines (C1–C7, C4 v1 complete), and 10 own-data engines:

### Scanner Engines
- **Legacy tiered scanner** (`service/scan.py`) — free/pro/elite tiered token scans
- **UnifiedTokenScanner** (`unified/scanner.py`) — DataBus-powered, own-data-first (now the primary engine)
- **SENTINEL pipeline** (`modules/`) — 17-core deep forensic modules
- **DegenSecurityScanner** (`degen_security_scanner/`) — 7-phase Solana degen scanner

### Capability Engines (C1–C7, from `docs/scan-capabilities-spec.md`)
- **C1: Wallet-to-Wallet Flow Visualization** — `engines/wallet_flow.py` (evm_transfers + wallet_labels)
- **C2: BubbleMaps-style Holder Clustering** — `engines/holder_clusters.py` (evm_transfers + dex_events + wallet_labels)
- **C3: New Token Discovery + Early Warning** — `engines/new_token_radar.py` (deployer_events)
- **C5: Hype Score** — `engines/hype_score.py` (dex_events + evm_transfers + trending)
- **C6: Time-Based Attack Detection** — `engines/attack_detector.py` (sandwich/pump-dump/flash loan)
- **C7: Deployer Behavioral Fingerprint** — `engines/deployer_fingerprint.py` (deployer_events + dex_events + evm_transfers + wallet_labels)
- **F2: Price/Decimals Oracle (foundation)** — `engines/price_oracle.py` (7-tier USD resolution)
- **C4: Cross-Chain Entity Resolution** — ✅ v1 complete (`engines/cross_chain_entity.py`: same-deployer + same-funder links from ClickHouse with confidence scoring + evidence; behavioral fingerprint + bridge/launderer classification "coming in v2")

### Own-Data Engines (lite indexer powered)
- `own_security_analysis` — ClickHouse security patterns
- `own_lp_lock` — LP lock/burn detection
- `own_price_oracle` — price/reserve ratio from dex_events
- `own_holder_analysis` — holder concentration + HHI
- `own_bundle_detect` — bundle/sniper detection
- `own_copycat_detect` — copycat token detection
- `own_deployer_reputation` — deployer risk scoring
- `own_whale_detect` — whale wallet classification
- `own_volume_authenticity` — wash trading detection
- `own_holder_age` — holder age distribution

All 10 engines query ClickHouse directly (the lite indexer). Supporting files in
`engines/` include `own_dex_data/`, `insider_detector/`, `gmgn_client.py`,
`known_entities.py`, `intel_aggregation.py`, and `rpc_router.py`.

### DataBus Integration
- 115 provider chains registered (6 categories: market, security, wallet,
  intel, social, system), 176 total providers (live registry build, 2026-08-03)
  [REVIEW: doc previously reported 114/175; live `build_provider_chains()` counts 115/176]
- 29 distinct API key env vars across the app [REVIEW: audit reported 25]
- 3 free API fallback chains (token_security_free, wallet_labels_free, market_price_free)
- PREFER_OWN_DATA=true by default

## Entry Points
- HTTP API v1: `/api/v1/scanner/recent`, `/api/v1/data/scan-history`
- HTTP API v2: `/api/v2/scanner/token/scan`, `/api/v2/scanner/wallet/scan`
- Redis task queue: `app/core/task_queue.py` — background worker loop with
  exponential-backoff retry and dead-letter queue
  [REVIEW: audit describes "worker.py (uses UnifiedTokenScanner)"; no such file
  exists. The Redis queue (`task_queue.py`) currently has zero registered task
  handlers. `app/temporal/worker.py` exists but registers no workflows/activities.]
- Telegram: `/scan`, `/wallet`, `/investigate`, inline mode, alerts
- WebhookEngine: 7 providers (whale_move, token_deployed, price_change,
  first_swap, liquidity_change, new_block, wallet_activity)

## Capability Matrix

| Capability | Status | Engine | Data Sources |
|---|---|---|---|
| C1: Wallet-to-Wallet Flow Visualization | 🟢 Working | wallet_flow | evm_transfers + wallet_labels |
| C2: BubbleMaps-style Holder Clustering | 🟢 Working | holder_clusters | evm_transfers + dex_events + wallet_labels |
| C3: New Token Discovery + Early Warning | 🟢 Working | new_token_radar | deployer_events + C7 fingerprint |
| C4: Cross-Chain Entity Resolution | 🟢 v1 complete (v2 pending) | cross_chain_entity | deployer_events + evm_transfers (same-deployer / same-funder links) |
| C5: Hype Score | 🟢 Working | hype_score | dex_events + evm_transfers + trending |
| C6: Time-Based Attack Detection | 🟢 Working | attack_detector | dex_events (sandwich/pump-dump/flash loan) |
| C7: Deployer Behavioral Fingerprint | 🟢 Working | deployer_fingerprint | deployer_events + dex_events + evm_transfers + wallet_labels |
| Honeypot detection | Working | Multiple implementations | GoPlus + DexScreener + own data |
| Rugpull prediction | Working | SENTINEL composite scoring | 16 weighted modules (holder, bundle, funding, liquidity, dev rep, wash, etc.) [REVIEW: audit says "5 modules"; verified weight table covers 16] |
| LP lock/burn | Working | own ClickHouse + SENTINEL liquidity verifier | UNCX/TeamFinance/PinkLock/Mudra [REVIEW: specific locker providers not verified in code] |
| Ownership | Working | SENTINEL contract authority | Helius + Etherscan [REVIEW: provider specifics not verified] |
| Token security | Working | GoPlus + own ClickHouse | GoPlus, own_security_analysis |
| Holder analysis | Working | own ClickHouse + HolderAnalyzer | own_holder_analysis, DexScreener |
| MEV sandwich | Working | SENTINEL MEVDetector | Ported to ClickHouse [REVIEW: port target not verified] |
| Flash loan | Working | SENTINEL FlashLoanDetector | Pattern-based from dex_events [REVIEW: source not verified] |
| Wash trading | Working | SENTINEL WashTradingDetector + own volume authenticity | Cycle detection [REVIEW: algorithm detail not verified] |
| Bundle/sniper | Working | own ClickHouse + degen phase 5 | own_bundle_detect, DexScreener |
| Clone/copycat | Working | own ClickHouse | own_copycat_detect |
| Cross-chain trace | Working | own ClickHouse + CrossChainTraceAnalyzer | ClickHouse, SENTINEL |
| Wallet drain | Working | 22 tests | tests/unit/test_wallet_drain_scanner.py |
| Wallet risk scoring | Working | UnifiedTokenScanner | DataBus chains |
| Social/sentiment | Partial | SENTINEL sentiment + social velocity | 3 pass placeholders [REVIEW] |
| Contract verification | Working | Sourcify + Etherscan [REVIEW: in-repo Sourcify call not verified] | — |
| Proxy detection | Working | SENTINEL ProxyDetector | EIP-1967/1822/UUPS [REVIEW: standards not verified] |
| Deployer reputation | Working | own ClickHouse (new!) | own_deployer_reputation |
| Whale detection | Working | own ClickHouse (new!) | own_whale_detect |
| Volume authenticity | Working | own ClickHouse (new!) | own_volume_authenticity |
| Holder age | Working | own ClickHouse (new!) | own_holder_age |

## Data Sources

| Source | Type | What It Provides |
|---|---|---|
| `chains/chain_indexer_own.py` | Own (ClickHouse) | LOCAL-tier providers; preferred when PREFER_OWN_DATA=true (default) |
| `chains/chain_indexer.py` | Own (ClickHouse) | Indexed chain data (dex_events, transfers) |
| `chains/dexscreener.py` | Free | Token pairs, liquidity, trades, top traders, trending |
| `chains/free_apis.py` | Free | CoinGecko, DeFiLlama, Blockchair, Etherscan (no key required) |
| `chains/free_security.py` | Free | token_security_free, wallet_labels_free, market_price_free fallback chains |
| `chains/freemium_apis.py` | Free/paid hybrid | Freemium provider chains with fallback |
| `chains/alchemy.py` | Paid | EVM token balances, metadata |
| `chains/arkham.py` | Paid | Entity intel, labels, transfers, counterparties, portfolio |
| `chains/moralis.py` | Paid | EVM wallet/token data |
| `registry/categories/*.py` | All | Builds every fallback chain by data type (market, security, wallet, intel, social, system) |
| `local.py` / `mcp_bridge.py` | Local | Wallet Memory Bank, ClickHouse, RugBrain (Redis RAG support store), MCP bridge |

### 5-Tier Free API Fallback Chains (no keys required)
- **token_security_free**: ClickHouse → GoPlus → Honeypot.is → RugCheck → DexScreener
- **wallet_labels_free**: 39M labels → Blockscout → Etherscan → DexScreener → heuristics
- **market_price_free**: own TWAP → DexScreener → CoinGecko → GeckoTerminal → ParaSwap

Each tier is a verified-live free provider with circuit breaker + rate limiter;
own-data (LOCAL tier) always wins when `PREFER_OWN_DATA=true`.

All documented in the provider registry — LOCAL FIRST → FREE SECOND → PAID LAST → NEVER WASTE (pool rotation, rate limits, monthly quota tracking).

## RugBrain RAG

> Brand: **RugBrain** — the crypto-native RAG engine. Full architecture in
> `docs/RAG_ARCHITECTURE.md`; quick start in `docs/RUGBRAIN.md`.

The RAG layer that backs scanner threat-intelligence matching is the RugBrain stack:

- **Embedding + reranking** — Infinity (`infinity-rag:8001`): `BAAI/bge-m3`
  (1024-dim dense) for embedding, `BAAI/bge-reranker-v2-m3` cross-encoder for
  two-stage reranking (fail-open)
- **Vector storage** — Qdrant (`rmi-qdrant:6333`): **18 collections**, **4,354+ points**
  across security / code / market / news domains
- **Orchestration** — LangGraph agentic graph (`app/routers/rag_v2.py`) for
  `/api/v1/rag/v2/ask`: real-time vs historical routing, tool-use (indexer RPC,
  RugMunchScan rescan, news/RSS fetch), grounded synthesis with citations
- **News ingestion** — Crawl4AI + pryscraper bridge (3-layer extraction:
  trafilatura → FlareSolverr → Chromium)
- **Evaluation** — Ragas metrics on a crypto golden set (faithfulness, answer
  relevance, context precision/recall) gated in CI on every RugBrain change
- **Observability** — Phoenix (Arize) tracing via OpenInference
  (`app/rag/tracing.py`), project `rugmunch-rag`
- **Support stores** — Redis (`rag:hashes:*` dedup, embed cache, rate limits);
  Postgres as the source of truth for envelopes (Qdrant is a projection)

API surface: `POST /api/v1/rag/v2/search` · `/ingest` · `/feedback` ·
`/bulk-ingest`, `GET /api/v1/rag/v2/stats`, `POST /api/v1/rag/v2/ask` (+SSE),
`GET /api/v1/rag/metrics`.

## Recent Changes (2026-08-03)

- `c6f521fc` feat(scanner): C4 cross-chain entity v1 complete, finish `_common` extraction, fix 30 legacy F821 NameErrors
- `2a7cdf89` perf(scanner): read token symbols from `token_meta_ref`, not `evm_transfers`
- `771a0e03` refactor(scanner): extract shared engine code to `_common.py`, fix 5 review bugs
- `71180ab9` fix(scanner): resolve 7 F821 NameErrors, narrow 55 broad excepts, add 64 tests, update docs

## Recent Changes (2026-08-02)

- `2a7cdf89` feat(pryscan): C4 cross-chain entity resolution v1 stub engine (`engines/cross_chain_entity.py`, 490 lines) + DataBus LOCAL provider `cross_chain_entity` + read symbols from `token_meta_ref`
- `4ad65480` feat(pryscan): C2 BubbleMaps-style holder clustering engine (1,019 lines, 38 tests)
- `645f2c89` feat(pryscan): C1 wallet flow, C3 new token radar, C5 hype score (17 tests)
- `cfe29dc8` feat(indexer): deployer tracker CLI entry point + systemd timer (15-min sweeps)
- `7432c654` feat(pryscan): F2 price oracle, C7 deployer fingerprint, C6 attack detection, quality fixes (20 C6 tests)
- `28fea95d` feat(pryscan): own-data engines, free API fallback, bug fixes, capabilities spec
- `bc5e5263` feat(scanner): god function splits, 120 new tests, DataBus chains, dead code cleanup
- `35c31599` feat(scanner): wire Telegram alerts, start WebhookEngine, fix credentials, unify scanner
- `d3c66581` feat(health): add RAG pipeline stats to health endpoint
- `117994c4` security(gcp): nuke Google Cloud integration after $90 bill
- `28c83130` fix(telegram,x402): authenticate bot→backend calls, fix OpenAPI 500, fix NameErrors

### Lite Indexer v1.3.0 (2026-08-02, `lite-chain-indexer` / `app/domains/indexer/`)
- **Address-first wallet access path** (`storage/projections.py`) — electrs-inspired
  projections on `evm_transfers` ordered by `(chain, address, block_number, log_index)`;
  `wallet_out_proj` (from_address) + `wallet_in_proj` (to_address) with direction labels;
  backfill existing rows via `materialize_existing=True`; gated by `WALLET_PROJECTION_ENABLED`
- **Token metadata reference table** (`storage/token_meta.py`) — `wallet_memory.token_meta_ref`,
  one row per token instead of symbol/name on every transfer row (~60 bytes × 264M rows);
  gated by `TOKEN_META_ENABLED`
- **Per-token incremental balance cache** (`storage/balance_cache.py`) — `holder_balances`
  (ReplacingMergeTree) read-modify-written per holder per batch; `get_token_holders()` serves
  top-holder queries from pre-computed answer; gated by `BALANCE_CACHE_ENABLED`
- **Auto-maintained holder balances** (`storage/balance_cache.py`) — `holder_balances_mv`,
  SummingMergeTree materialized view fed by `evm_transfers` inserts; gated by `HOLDER_BALANCES_MV_ENABLED`
- Indexer wiring: `ClickHouseWriter.write_wallet_features()` runs after transfer inserts succeed;
  each feature independently gated, all DDL idempotent and non-fatal

### Lite Indexer v1.2.0 (2026-08-02, `lite-chain-indexer`)
- **F0 Deployer EOA fix** — deployer events now resolve the real deployer EOA
  (transaction `from` address via `eth_getTransactionByHash`) instead of the
  factory address; batched with bounded semaphore, graceful fallback
- Bloom pre-filtering (block header bloom cache → fewer RPC round trips)
- Chunked indexing (50 EVM blocks / 500 Solana slots per `eth_getLogs` call)
- Chain parallelism (CPU affinity, per-chain memory budgets + priorities)
- Column TTL (30–50% storage cut) + Parquet cold tier (B2 upload, DuckDB query)

## In Progress / Planned

Prioritized from `docs/scan-capabilities-spec.md`:

### P0 — Foundations
- **F0: Deployer EOA Fix** — ✅ DONE (lite indexer v1.2.0) — foundation for C3, C7, C4
- **F1: Solana Owner Resolution** — foundation for C2, C4, C5 (pending)
- **F2: Price/Decimals Oracle** — ✅ DONE (`engines/price_oracle.py`) — foundation for C1, C2, C5, C6

### P0 — Capabilities
- **C1: Wallet-to-Wallet Flow Visualization** — ✅ DONE — flagship investigative feature
- **C3: New Token Discovery + Early Warning** — ✅ DONE — core "early warning" spine
- **C6: Time-Based Attack Detection** — ✅ DONE — core differentiator, pure own-data
- **C7: Deployer Behavioral Fingerprint** — ✅ DONE

### P1 — Capabilities
- **C2: BubbleMaps-style Holder Clustering** — ✅ DONE — enrichment/differentiator
- **C4: Cross-Chain Entity Resolution** — ✅ v1 DONE (same-deployer + same-funder links, `cross_chain_entity.py`); v2 pending: behavioral fingerprint + bridge/launderer classification
- **C5: Hype Score** — ✅ DONE — enrichment

Build order and sequencing documented in the spec (`docs/scan-capabilities-spec.md`).

## Quality Metrics
- 452 scanner tests in `tests/unit/domain/scanner/` (live pytest collection 2026-08-03,
  26 test files); 3,690 tests across the full suite; 532 scanner-related
  tests by filename pattern (unit scanner domain + test_scanner* + scan/rugmaps/wallet scan)
- 0 bare excepts; 118 broad `except Exception` in scanner domain
  [REVIEW: doc previously reported 70; new capability engines added
  try/except-wrapped code. 0 remaining in clone_scanner,
  own_copycat_detect, chain_pattern_detector after narrowing]
- 7 F821 (undefined-name) bugs found in review — all fixed; `ruff --select F821`
  on scanner domain now passes with 0 findings (verified 2026-08-03)
- Legacy F821 cleanup in progress: `app/domains/scanners/` at **76 findings
  (was 106)** — 30 fixed in the 2026-08-03 wave (`address_labeler.py`,
  `block_zero_sniper.py`, `wash_trading.py` got missing `requests`/`json`
  imports); `app/domains/databus/` is clean (0 findings)
  [REVIEW: legacy count still 76 — only `app/domains/scanner/` (new engine
  domain) is at 0. Databus verified 0 on 2026-08-03]
- 3 god functions split in the 2026-08-02 refactor
  [REVIEW: audit says "3 remaining >100 lines"; current AST scan of
  `app/domains/scanner/` finds 37 functions >100 lines domain-wide]
- Type hints + docstrings throughout

## Repositories
- Scanner: rmi-backend/app/domains/scanner/
- Own-data engines: rmi-backend/app/domains/scanner/engines/own_*.py
- Capability engines (C1–C7): rmi-backend/app/domains/scanner/engines/{wallet_flow,holder_clusters,new_token_radar,hype_score,attack_detector,deployer_fingerprint,price_oracle,cross_chain_entity}.py
- Lite indexer: lite-chain-indexer/ (v1.3.0) — re-exported via rmi-backend/app/domains/indexer/
- DataBus: rmi-backend/app/domains/databus/
- Telegram: rmi-backend/app/domains/telegram/rugmunchbot/
- Spec: rmi-backend/docs/scan-capabilities-spec.md

---
Last updated: 2026-08-03
