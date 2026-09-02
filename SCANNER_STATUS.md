# RugMunchScan — Status & Capabilities

> Living document. Last updated: 2026-09-02

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

**Chain coverage (2026-09-02):**
- **Solana modules NOW WORK** — the case-aware fix (`normalize_addr`
  preserves base58 case; queries hit `solana_spl_balance_changes` with the
  `mint` column). Previously reported "unavailable" because engines
  lowercased Solana addresses and/or queried `evm_transfers` for Solana.
- **EVM modules NOW WORK** — the address-normalization fix (EVM hex
  lowercased consistently) plus correct `evm_transfers` column mapping.
- **Per-query ClickHouse clients** — no more "simultaneous queries on
  single connection" errors from the shared client singleton (see the
  resilience layer's `ConnectionPool` above).

### Chain Data Access Layer (2026-09-02)
- **`engines/_chain_data.py`** — THE single source of truth for
  chain→table→column mapping. `get_chain_spec(chain) -> ChainDataSpec`
  returns table names (`transfers_table`, `dex_table`), column names
  (`addr_col`, `from_col`, `to_col`, `amount_expr`, `ts_col`, `tx_hash_col`,
  `block_col`), the `chain_filter` WHERE fragment, and case sensitivity
  (`is_solana`). Raises `ValueError` for unknown chains. Tron has no
  ClickHouse transfers table (`has_transfers_table=False`) — TronGrid
  provider instead.
- **`normalize_addr(addr, chain)`** — the ONLY correct way to normalize an
  address: EVM hex → lowercase; Solana base58 → case-preserving (strip
  whitespace only). This fixed the EVM own-data engines that were
  lowercasing Solana addresses (and vice versa) and returning nothing.
- **Contract:** all engines MUST import and use this module — no hardcoded
  table names in engine code. Migration is incremental; new code must never
  add inline `if chain == "solana": ...` table branches.

### Scan Orchestration (`unified/scanner.py`)
- **`SCAN_HARD_TIMEOUT_S = 120`** (env-overridable; was 25 → 60 → 120).
  The HTTP scan response returns within this budget while deep/intel/
  SENTINEL/news phases continue in a shielded background task; progress +
  final result publish to Redis (`scanner:status:{chain}:{address}`, TTL
  15 min) and are polled via `GET /api/v2/scanner/token/scan/{chain}/{address}/status`.
- **Error visibility (2026-09-02 hardening):** `ScanResult.errors` is a
  structured list of `{"module", "chain", "error"}` entries. Helpers
  `_record_module_error()` (WARNING log + errors entry) and
  `_record_module_unavailable()` (INFO log + errors entry) in
  `unified/_phase_helpers.py` ensure no module failure is ever silent —
  the API consumer sees WHY own-data was missing.
- **Honest data_sources counting:** `wallet_analysis` only appends
  `wallet_analysis:own` to `enrichment_sources` when at least ONE of its 4
  metric engines (funding, freshness, overlap, metrics) returned
  `"scanned"` — a ClickHouse outage no longer inflates own-data counts.
- **Wallet-analysis phase (2026-09-02 fix):** the 4 metric engines run
  sequentially (was one giant gather of ~25 concurrent heavy ClickHouse
  queries); per-wallet freshness is bounded to top-5 holders at
  concurrency 3; holders fall back to the engine's own ClickHouse
  top-holders query when the DataBus `holder_data` chain lacks
  `top_holders`.

### Resilience Layer (rugmunch-scanner, `rugmunch_scanner/resilience.py`)
- **`retry_on_transient`** — async decorator: exponential backoff + jitter
  on ConnectionError/TimeoutError/OSError/ClickHouse OperationalError;
  never retries ValueError/TypeError.
- **`with_timeout`** — hard cap via `asyncio.wait_for`; returns a
  structured `{"result": "timeout", ...}` fallback so timeouts are
  distinguishable from real data.
- **`HealthTracker`** — thread-safe per-module health: degraded after 5
  consecutive failures, recovers on next success.
- **`ConnectionPool`** — pool of ClickHouse clients (one connection per
  concurrent query, max 10, bounded acquire via `with pool.acquire()`),
  replacing the shared single client that caused "simultaneous queries on
  single connection" errors under load. Broken clients are discarded, not
  recycled. All primitives are fail-open: infrastructure problems degrade
  results, never crash a scan.

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

## Current Limitations (2026-09-02)

Honest limitations — these are data/infrastructure constraints, not bugs:

- **EVM first_buyers on USDT-scale tokens** — tokens with ~26.5M transfer
  rows hit the ClickHouse 10s server-side timeout on the first-buyers
  query; the module degrades to `unavailable` (recorded in `errors`).
- **EVM dex_events 4–9 days stale** — catch-up backlog in the indexer,
  not a failure. Solana dex_events are fresh.
- **holder_overlap + bundle_analysis return "unavailable"** for tokens
  without scam-token holder sets or launch indexing data — an honest data
  limitation (no reference data to compare against), not a bug. Recorded
  in `errors` with the reason.
- **Solana native SOL transfers stale (Aug 24)** — the `solana_transfers`
  table stopped updating Aug 24; only SPL balance changes
  (`solana_spl_balance_changes`) are fresh. Native-SOL flow analysis is
  degraded until the indexer catches up.

## Response Fields (`_format_scan_response`, unified_scanner_router.py)

Every field in the `/api/v2/scanner/token/scan` (+ `/token/deepscan`) response:

| Field | Source |
|---|---|
| `status` | always `"ok"` |
| `token`, `chain` | ScanResult identity |
| `name`, `symbol` | token metadata |
| `price_usd`, `market_cap_usd`, `liquidity_usd`, `volume_24h_usd`, `age_hours` | core identity |
| `safety_score`, `confidence`, `risk_level` | scoring |
| `risk_flags`, `green_flags` | scoring evidence |
| `breakdown`, `evidence` | per-dimension score breakdown + raw findings |
| `modules_run` | list of module names that ran |
| `errors` | structured `[{module, chain, error}]` — WHY own-data is missing (2026-09-02) |
| `scan_duration_ms`, `tier`, `scanned_at` | metadata |
| `background_status` | `"complete"` or `"running"` (poll status endpoint) |
| `cached`, `stability` | 1h result-cache hit + risk-level agreement summary |
| `data_source`, `freshness` | `"databus"` / scan timestamp |
| `fallback_used` | true when external fallbacks ≥ own-data modules |
| `deployer_address`, `deployer_source` | canonical top-level deployer (2026-08-25) |
| `data_sources` | `{own_data_modules, external_fallback_modules, total_modules}` |
| `rag_context` | RugBrain RAG matches (similar_scams, wallet_context, fusion_summary) |
| `news_sentiment`, `news_card` | RugBrain news engines |
| `sentinel` | SENTINEL phase results (`{free, premium, ...}`) |
| `wallet_metrics`, `funding_analysis`, `wallet_freshness`, `holder_overlap` | own-data wallet engines (surfaced 2026-09-02) |
| `bundle_analysis`, `first_buyers` | own-data launch engines |
| `risk_report` | v2 structured ~30-factor RiskReport + wallet-cluster graph |
| `holder_concentration`, `top_holders`, `holder_count` | holder_clusters engine (rich-output block) |
| `contract_authority` | SENTINEL free module |
| `bundler_count`, `bundled_first_buyers`, `bundler_pct` | bundle + first-buyers derived counts |
| `wallet_metrics_summary` | `{holders_analyzed, fresh_wallet_pct, cex_funded_pct, first_tx_pct}` |
| `deep_links` | chain-aware solscan/etherscan/dexscreener/gmgn/defined/geckoterminal links |
| `deployer_profile` | deep-tier SENTINEL deployer profile |
| `ai_verdict` | attached at the async route layer (fail-open) |

## Recent Changes (2026-09-02) — Scanner Quality Push

- **Solana case-aware fix** — `normalize_addr` preserves base58 case;
  Solana own-data engines work again.
- **EVM address normalization** — consistent lowercase EVM matching;
  EVM own-data engines work again.
- **Chain data access layer** — `engines/_chain_data.py` single source of
  truth for chain→table→column mapping.
- **Resilience layer** — retry/timeout/health-tracking/connection-pool in
  rugmunch-scanner `resilience.py`.
- **Error visibility** — `ScanResult.errors` + `_record_module_error()` /
  `_record_module_unavailable()`; honest data_sources counting.
- **Wallet-analysis fix** — sequential metric engines, bounded freshness
  concurrency (top-5 @ 3), holder fallback to own ClickHouse.
- **`SCAN_HARD_TIMEOUT_S` 25 → 60 → 120** — own-data phases now fit the
  HTTP budget on large tokens.
- **dex_events parsing fixes** — EVM dex_events parsing corrected
  (4–9 day catch-up backlog remains, see Current Limitations).

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
Last updated: 2026-09-02
