# RugMunchScan — Status & Capabilities

> Living document. Last updated: 2026-08-02

## Architecture

RugMunchScan has 4 scanner engines and 10 own-data engines:

### Scanner Engines
- **Legacy tiered scanner** (`service/scan.py`) — free/pro/elite tiered token scans
- **UnifiedTokenScanner** (`unified/scanner.py`) — DataBus-powered, own-data-first (now the primary engine)
- **SENTINEL pipeline** (`scanners/sentinel_pipeline/`) — 17-core deep forensic modules
- **DegenSecurityScanner** (`degen_security_scanner/`) — 7-phase Solana degen scanner

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
- ~119 provider chains registered across 6 categories (market, security, wallet,
  intel, social, system) [REVIEW: audit reported 107; current registry scan counts 119]
- 29 distinct API key env vars across the app [REVIEW: audit reported 25]
- 3 free API fallback chains (token_security, wallet_labels, market_price)
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
| `local.py` / `mcp_bridge.py` | Local | Wallet Memory Bank, ClickHouse, Redis RAG, MCP bridge |

All documented in the provider registry — LOCAL FIRST → FREE SECOND → PAID LAST → NEVER WASTE (pool rotation, rate limits, monthly quota tracking).

## Recent Changes (2026-08-02)

- `28fea95d` feat(pryscan): own-data engines, free API fallback, bug fixes, capabilities spec
- `bc5e5263` feat(scanner): god function splits, 120 new tests, DataBus chains, dead code cleanup
- `35c31599` feat(scanner): wire Telegram alerts, start WebhookEngine, fix credentials, unify scanner
- `d3c66581` feat(health): add RAG pipeline stats to health endpoint
- `117994c4` security(gcp): nuke Google Cloud integration after $90 bill
- `28c83130` fix(telegram,x402): authenticate bot→backend calls, fix OpenAPI 500, fix NameErrors

## In Progress / Planned

Prioritized from `docs/scan-capabilities-spec.md`:

### P0 — Foundations (F0–F2)
- **F0: Deployer EOA Fix** — foundation for C3, C7, C4
- **F1: Solana Owner Resolution** — foundation for C2, C4, C5
- **F2: Price/Decimals Oracle** — foundation for C1, C2, C5, C6 (every USD-denominated output)

### P0 — Capabilities
- **C1: Wallet-to-Wallet Flow Visualization** — flagship investigative feature
- **C3: New Token Discovery + Early Warning** — core "early warning" spine
- **C6: Time-Based Attack Detection** — core differentiator, pure own-data
- **C7: Deployer Behavioral Fingerprint**

### P1 — Capabilities
- **C2: BubbleMaps-style Holder Clustering** — enrichment/differentiator
- **C4: Cross-Chain Entity Resolution** — depends on foundations
- **C5: Hype Score** — enrichment

Build order and sequencing documented in the spec (`docs/scan-capabilities-spec.md`).

## Quality Metrics
- ~3,500 tests collected (`pytest --collect-only`, 2026-08-02)
  [REVIEW: audit reported ~930; current collection is 3,536]
- 0 bare excepts; 70 broad `except Exception` in scanner domain
  [REVIEW: audit reported 62, down from 90]
- 3 god functions split in the 2026-08-02 refactor
  [REVIEW: audit says "3 remaining >100 lines"; current AST scan of
  `app/domains/scanner/` finds 37 functions >100 lines domain-wide]
- Type hints + docstrings throughout

## Repositories
- Scanner: rmi-backend/app/domains/scanner/
- Own-data engines: rmi-backend/app/domains/scanner/engines/own_*.py
- DataBus: rmi-backend/app/domains/databus/
- Telegram: rmi-backend/app/domains/telegram/rugmunchbot/
- Spec: rmi-backend/docs/scan-capabilities-spec.md
