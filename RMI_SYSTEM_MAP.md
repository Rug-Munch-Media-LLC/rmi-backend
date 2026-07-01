# Rug Munch Intelligence (RMI) — System Map & Build Status

## LIVE SYSTEM OVERVIEW

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (React)                      │
│  /root/frontend/ — 20 pages, dist/index.html            │
│  RugMaps, RugCharts, Alerts, Markets, News,            │
│  Intelligence, Investigation, ScamSchool, MCP Docs      │
└────────────────────┬────────────────────────────────────┘
                     │ Supabase + REST API
┌────────────────────▼────────────────────────────────────┐
│                  BACKEND (FastAPI)                       │
│  /root/backend/ — 379 endpoints, 6 routers              │
│  Docker: rmi_backend (volume-mounted /app/app)         │
│  66 backend modules, 8 data connectors                  │
│                                                          │
│  WALLET-CLUSTERING ROUTER (14 endpoints)                │
│    POST /contract-scan → holders → clusters → bundles    │
│    POST /cluster/detect → 7-method detection             │
│    POST /cluster/analyze → behavioral fingerprinting     │
│    GET  /health → cache + GNN + spam stats               │
│                                                          │
│  FORENSICS ROUTER (12 endpoints)                         │
│    POST /threat-check → CryptoScamDB + GoPlus + Januus    │
│    POST /deep-scan → full wallet forensics               │
│    POST /cross-chain → multi-chain correlation           │
│                                                          │
│  RUGMAPS ROUTER (8 endpoints)                            │
│    GET  /analyze/{address} → bubble map generation       │
│    GET  /health                                           │
│                                                          │
│  CROSS-TOKEN ROUTER (8 endpoints)                         │
│    GET  /connections/{wallet} → cross-project links       │
│                                                          │
│  DISCOVERY ROUTER (8 endpoints)                           │
│    GET  /tokens → new token discovery                     │
│                                                          │
│  X402 TOOLS ROUTER (142 endpoints)                       │
└────────────────────┬────────────────────────────────────┘
                     │
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────────┐
│ Helius x3 │  │QuickNode │  │ DexScreener  │
│ (primary) │  │(fallback)│  │  (free tier) │
└──────────┘  └──────────┘  └──────────────┘
      ▼              ▼              ▼
┌─────────────────────────────────────────────────────────┐
│              UNIFIED PROVIDER (7-source cascade)        │
│  Helius → Birdeye → Solscan → GMGN → DexScreener →   │
│  QuickNode → Blockchair    Rate-limited 5 req/sec      │
└─────────────────────────────────────────────────────────┘
```

## DATA SOURCES (18 API Keys)

| Source | Key File | Purpose | Status |
|---|---|---|---|
| Helius x3 | helius_api_key, _2, _3 | Solana RPC primary | LIVE |
| QuickNode | quicknode_api_key | Solana RPC fallback | LIVE |
| Birdeye | birdeye_api_key | Token data, whale tracking | LIVE |
| GMGN | gmgn_api_key | Token discovery | LIVE |
| Moralis | moralis_api_key | Multi-chain EVM data | CONFIGURED |
| Arkham | arkham_api_key | Entity labeling | CONFIGURED |
| CoinGecko | coingecko_api_key | Price data | CONFIGURED |
| Dune | dune_api_key | SQL queries on-chain | CONFIGURED |
| Nansen | nansen_api_key | Smart money tracking | CONFIGURED |
| Solscan | solscan_api_key | Solana transaction data | CONFIGURED |
| NVIDIA | nvidia_api_key, dev_api_key | AI inference | CONFIGURED |
| OpenRouter | openrouter_api_key | LLM routing | CONFIGURED |
| Groq | groq_api_key | Fast LLM inference | CONFIGURED |
| SiliconFlow | siliconflow_api_key, _2 | LLM inference | CONFIGURED |
| Kimi | kimi_api_key | LLM (Moonshot) | CONFIGURED |
| Mistral | mistral_api_key | LLM inference | CONFIGURED |
| Gemini | gemini_api_key | Google AI | CONFIGURED |
| HuggingFace | huggingface_token | Model downloads | CONFIGURED |
| Cloudflare | cloudflare_api_token | Workers, DNS | LIVE |
| Telegram | telegram_bot_token | Bot integration | LIVE |

## DETECTION PIPELINE

```
Token/Address Input
        │
        ▼
┌──────────────────┐
│ ENTITY REGISTRY  │ ← 50+ CEX/DeFi/Mixer addresses
│ filter_infra()   │ ← 100K+ Solana labels from CSV
└────────┬─────────┘
         ▼
┌──────────────────┐
│ SPAM REGISTRY    │ ← 2,530 Scam Sniffer addresses
│ check_token()    │ ← GoldRush 8M spam tokens (6 chains)
└────────┬─────────┘ ← OpenSanctions OFAC, Guardian phishing
         ▼
┌──────────────────┐
│ HOLDER ANALYSIS  │ ← Helius getProgramAccounts (228K JTO)
│ (unified_provider)│ ← Multi-source fallback cascade
└────────┬─────────┘
         ▼
┌──────────────────┐
│ BUNDLE DETECTION │ 5 signals:
│ (bundle_detector)│ ← atomic_block, common_funder, temporal,
└────────┬─────────┘ ← distribution_anomaly, concentration
         ▼
┌──────────────────┐
│ CLUSTER DETECTION│ 7 methods:
│ (wallet_clustering)│ ← temporal, counterparty, behavioral,
└────────┬─────────┘ ← funding, pattern, ML similarity, sleeper
         ▼
┌──────────────────┐
│ GNN FRAUD SCORE │ ← Random Forest fallback (CPU-only)
│ (fraud_gnn)      │ ← HuggingFace sklearn (gated, not loaded)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ THREAT INTEL     │ ← CryptoScamDB (MIT, free)
│ (threat_feeds)   │ ← GoPlus Security (free tier)
└────────┬─────────┘ ← Januus risk scores (open-source)
         ▼
┌──────────────────┐
│ CROSS-CHAIN      │ ← Behavioral fingerprinting
│ (correlator)     │ ← CEX deposit pattern matching
└────────┬─────────┘ ← Union-find entity grouping
         ▼
    RISK SCORE OUTPUT
```

## LOCAL DATA FILES

| File | Lines | Purpose |
|---|---|---|
| wallet-labels/solana_cex_labels.csv | 100,001 | All known CEX hot wallets on Solana |
| wallet-labels/solana_defi_labels.csv | 1,481 | DeFi protocol addresses (Jupiter, Raydium, etc.) |
| wallet-labels/solana_dapp_labels.csv | 1,791 | Dapp addresses |
| wallet-labels/etherscan_malicious_labels.csv | 7,781 | Etherscan-flagged malicious contracts |
| wallet-labels/malicious_smart_contracts.csv | 754 | Additional malicious contracts |
| wallet-labels/ofac_sanctions.json | 0 | (Empty - needs seeding) |
| spam/scamsniffer_blacklist.json | 2,531 | Scam Sniffer address blacklist |
| SOSANA-CRM-2024.json | 101,916 | Full CRM data dump |
| wallet_database.json | 364 | Wallet profiles DB |
| rmi.db | 9 | SQLite state |

## SUPABASE TABLES (8 tables)

- profiles — user profiles
- wallet_labels — labeled wallet data
- token_analysis — token analysis results
- scam_reports — scam report submissions
- alerts — alert configurations
- market_intel — market intelligence cache
- news — news article cache
- forensic_reports — forensic analysis results

## INFRASTRUCTURE

| Service | Container | Status | Purpose |
|---|---|---|---|
| Backend API | rmi_backend | UP (healthy) | FastAPI, 379 endpoints |
| n8n Automation | rmi_n8n | UP | Workflow automation |
| Worker | rmi_worker | UP (healthy) | Background job processing |
| Telegram Bot | telegram-mcp | UP | Telegram bot integration |
| Listmonk | rmi-listmonk | UP | Email newsletters |
| Dragonfly (Redis) | rmi_dragonfly | UP (healthy) | Caching |
| Cloudflare Worker | rmi_cloudflare | UP | CF tunnel/edge |
| Ghost CMS | rmi-ghost | UP | Blog/content |
| MySQL | rmi-mysql | UP | Database |
| Langfuse | langfuse stack | UP | LLM observability |
| CF Edge Worker | rag.rugmunch.io | LIVE | RAG caching |

## WHAT'S WORKING (VERIFIED)

- [x] Helius RPC — 228K JTO holders detected via getProgramAccounts
- [x] Multi-source cascade — Helius → QuickNode → DexScreener fallback
- [x] Entity Registry — Binance/Uniswap/Tornado correctly identified
- [x] Bundle Detection — JTO = 0.09 confidence (correctly low)
- [x] Spam Registry — 2,530 Scam Sniffer addresses loaded
- [x] GNN Scoring — Random Forest fallback active (HuggingFace model gated)
- [x] Threat Feeds — GoPlus + CryptoScamDB + Januus integrated
- [x] All 5 health endpoints returning "ok"
- [x] All 10 core modules importing clean
- [x] RAG Edge Worker at rag.rugmunch.io returning health ok
- [x] n8n running with database
- [x] Telegram bot connected to Telegram servers

## WHAT'S BROKEN / NEEDS WORK

### Critical
- [ ] **RAG collections empty** — wallet_profiles, scam_patterns, forensic_reports all 0 docs. n8n needs to feed these. Only news_articles has 4 docs.
- [ ] **OFAC sanctions empty** — wallet-labels/ofac_sanctions.json is 0 lines. Needs seeding from opensanctions.org
- [ ] **563 uncommitted files** — backend has substantial uncommitted changes (Dockerfile, x402, entity_labeler, portfolio_tracker, etc.)
- [ ] **Frontend only has index.html** — 20 page source files exist but dist/ only has index.html. Other pages (docs, pricing, tools, x402) were deleted.

### High Priority
- [ ] **HuggingFace model gated** — fraud_gnn.py falls back to heuristic Random Forest because the sklearn model requires auth. Need to either get access or train a proper model.
- [ ] **n8n workflows not queryable** — 6 workflows claimed but API returned empty. Need to verify they're running.
- [ ] **CryptoGuard/GoPlus integration testing** — threat_feeds.py has the code but needs live testing with known scam addresses.
- [ ] **Entity labeler refactoring** — entity_labeler.py has 1084 lines of changes uncommitted, needs cleanup.
- [ ] **Exchange flow analyzer** — exchange_flow_analyzer.py reworked but uncommitted.

### Medium Priority
- [ ] **Frontend build pipeline** — Need to build and deploy the React frontend properly with all 20 pages.
- [ ] **Telegram bot features** — Bot is connected but needs command handlers for RMI features (scan, alert, etc.)
- [ ] **Email alerts** — Listmonk is running but not wired to RMI alert system.
- [ ] **CF Worker source** — rag.rugmunch.io is live but worker source code not in repo.
- [ ] **DexScreener connector** — Listed in unified_provider but not tested in cascade.
- [ ] **Blockchair connector** — Exists but not wired into unified_provider cascade.
- [ ] **EVM connector** — File exists but not tested against real EVM chains.

### Low Priority / Nice-to-Have
- [ ] **x402 payment system** — 142+ endpoints in x402_tools but uncommitted changes.
- [ ] **GNN model training** — Train a local sklearn model on known fraud data instead of HF gated model.
- [ ] **Cross-chain EVM testing** — cross_chain_correlator has Ethereum CEX addresses but Solana-only testing.
- [ ] **Mempool sentinel** — mempool_sentinel.py exists but unclear if active.
- [ ] **Wallet monitor** — wallet_monitor.py exists but not connected to alerts.

## BUILD PLAN — NEXT STEPS

### Phase 1: Stabilize & Commit (Day 1)
1. Commit all 563 uncommitted backend files
2. Seed RAG collections (wallet profiles from labels, known scam patterns)
3. Seed OFAC sanctions data from OpenSanctions
4. Test all threat feeds end-to-end with known scam addresses

### Phase 2: Frontend & Bot (Day 2-3)
5. Build frontend properly — `npm run build` in /root/frontend
6. Wire Telegram bot commands: /scan, /alert, /watch, /status
7. Deploy frontend to CF Pages or VPS

### Phase 3: Data Pipeline (Day 3-4)
8. Wire n8n workflows to feed RAG collections continuously
9. Set up scheduled GoldRush spam token sync (6 chains)
10. Set up OpenSanctions daily sync
11. Set up Scam Sniffer blacklist auto-update

### Phase 4: Testing & Hardening (Day 4-5)
12. End-to-end test with known scam tokens (not just JTO)
13. EVM chain testing with Ethereum addresses
14. Load testing on /contract-scan endpoint
15. Documentation: API docs, setup guide, architecture diagram

### Phase 5: Production (Day 5+)
16. Set up GitHub Actions CI/CD
17. Auto-deploy on merge to main
18. Monitoring (Langfuse, uptime checks)
19. Rate limiting on public endpoints
20. Authentication on sensitive endpoints

## KEY FILES QUICK REFERENCE

```
/root/backend/app/
├── chain_client.py          # Rate-limited Solana RPC (Helius + QuickNode)
├── chain_cache.py           # LRU cache with TTL (500 entries)
├── chain_feeder.py          # Wallet TX feeding into clustering engine
├── unified_provider.py      # 7-source data cascade
├── bundle_detector.py       # 5-signal bundle detection
├── entity_registry.py       # 50+ CEX/DeFi/Mixer address exclusion
├── threat_feeds.py          # CryptoScamDB + GoPlus + Januus
├── fraud_gnn.py             # Random Forest fraud scoring (CPU-only)
├── spam_registry.py         # 2,530 scam addresses + GoldRush integration
├── cross_chain_correlator.py # Multi-chain entity resolution
├── wallet_clustering.py     # 7-method clustering engine
├── cluster_detection.py     # Cluster detection orchestrator
├── bubble_maps.py           # RugMaps visualization engine
├── token_discovery.py       # New token scanning
├── rag_service.py           # RAG query service
├── routers/
│   ├── wallet_clustering_router.py  # 14 endpoints
│   ├── forensics_router.py          # 12 endpoints
│   ├── bubble_maps_router.py        # 8 endpoints
│   ├── cross_token_router.py        # 8 endpoints
│   ├── discovery_router.py          # 8 endpoints
│   └── ... (admin, chat, x402, etc.)
├── data/
│   ├── spam/scamsniffer_blacklist.json  # 2,531 lines
│   ├── wallet-labels/                   # 100K+ Solana labels
│   └── SOSANA-CRM-2024.json            # Full CRM dump
```
