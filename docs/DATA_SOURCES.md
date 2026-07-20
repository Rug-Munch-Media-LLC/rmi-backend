# RMI Data Sources — Complete Registry

> Every data source used by Rug Munch Intelligence, organized by tier.
> Last updated: 2026-07-20

---

## Tiers

| Tier | Cost | Strategy |
|------|------|----------|
| **LOCAL** | $0 | Our own data — instant, unlimited, always available |
| **FREE_API** | $0 | No API key required — free and fast |
| **FREEMIUM** | $0 (key) | Free tier with API key — limited monthly credits |
| **PAID** | $$$ | Paid API — precious credits, used only when necessary |

---

## 1. LOCAL (Our Own Data)

| # | Source | What It Provides | Storage | Access Pattern | Freshness |
|---|--------|-----------------|---------|----------------|-----------|
| 1 | **Wallet Memory Bank** | 190K+ wallet labels (exchange, scam, KOL, protocol) | Redis (`rmi:label:{chain}:{address}`) | Direct Redis get | Real-time |
| 2 | **SENTINEL Scanner** | Token security analysis: honeypot, mint, tax, liquidity, holder concentration, deployer risk | Redis cache (`sentinelscan:{chain}:{address}`) + ClickHouse | HTTP passthrough → `/api/v1/token/scan` | Cached 5 min |
| 3 | **RAG Knowledge Base** | 17K+ scam documents, multi-collection semantic search | Qdrant (9 collections) | HTTP passthrough → `/api/v1/rag/search` | Real-time |
| 4 | **ClickHouse Analytics** | Historical pricing, holder snapshots, whale alerts | ClickHouse cluster | Direct query | Real-time |
| 5 | **Redis Price Cache** | Recent token prices from all sources | Redis (`price:{token_lower}`) | Direct Redis get | TTL 60s |
| 6 | **Consensus RPC** | Multi-provider RPC fallback (Helius → Solana Labs → Lava → ZAN for Solana; DRPC → PublicNode → LlamaRPC for EVM) | In-process load balancer | `consensus_rpc.py` | Per-request |
| 7 | **News Aggregator** | 30+ RSS/API news sources, quality-scored, deduped, sentiment-tagged | In-process + Redis cache | HTTP passthrough → `/api/v1/news/combined` | Polled every 5 min |
| 8 | **Funding Tracer** | First-funder detection, fund flow graphs, entity clustering | Neo4j + ClickHouse | In-process (`cross_chain_trace.py`) | Real-time |
| 9 | **Price Consensus Engine** | Cross-referenced prices from 4+ sources with outlier removal | In-process | `price_consensus.py` | Per-query |
| 10 | **Alert Pipeline** | Real-time alerts from webhooks + scanner + on-chain monitors | Redis pub/sub + ClickHouse | `alert_pipeline.py` | Real-time |
| 11 | **Bulletin Board** | User-generated intelligence posts with reactions/comments | Postgres | API endpoint | Real-time |
| 12 | **Wallet Memory (ClickHouse)** | Stored wallet profiles, token holdings, transaction history | ClickHouse | Analytics queries | Historical |
| 13 | **Deployer History DB** | Deployer wallet profiles, token deployment records, scam linkage | Postgres + Neo4j | `deployer_history.py` | Real-time |
| 14 | **Entity Registry** | Cross-chain entity resolution — known individuals, protocols, exchanges | Redis + Neo4j | `entity_registry.py` | Real-time |
| 15 | **Labels (190K+)** | Compiled labels from Arkham, Nansen (Solscan), manual curation | Redis | `wallet_memory_bank` | Updated daily |
| 16 | **News Intel Pipeline** | Multi-source news with AI quality scoring, category tagging, sentiment | ClickHouse + Qdrant | `news_intelligence.py` | Real-time |
| 17 | **Social Intel** | X/Twitter KOL tracking, shill detection, sentiment analysis | Redis + Postgres | `social_intel.py` | Per-poll |

---

## 2. FREE_API (No Key Required)

| # | Source | Endpoint(s) | What It Provides | Rate Limit | Used In |
|---|--------|------------|-----------------|------------|---------|
| 1 | **DexScreener** | `api.dexscreener.com/latest/dex/tokens/{token}` | Token pairs, price, liquidity, volume, FDV, chain | 300 req/min | Token metadata, price, holders, trades lookup |
| 2 | **DeFiLlama** | `api.llama.fi/protocols`, `/chains` | Global TVL, chain TVL, top protocols, yields | None documented | Market overview, chain TVL data |
| 3 | **Jupiter** | `quote-api.jup.ag/v6/quote` | Solana DEX price quotes, route aggregation | 10 req/s | Solana token pricing, swap quotes |
| 4 | **CoinGecko (Free)** | `api.coingecko.com/api/v3/simple/token_price/...` | Token prices, market data, trending, global metrics | 10-30 req/min | Live prices, market overview, trending coins |
| 5 | **cryptocurrency.cv (CryptoNews)** | `cryptocurrency.cv/api/v1/news` | Free unlimited news, RSS feeds, no API key needed | Unlimited | News feed backup |
| 6 | **Blockchair** | `api.blockchair.com/{chain}/dashboards/address/{addr}` | Address balance, transaction count, chain stats (BTC, LTC, BCH, DOGE) | 3 req/s, no key needed for basic | Non-EVM chain data |
| 7 | **Fear & Greed Index** | `api.alternative.me/fng/` | Crypto Fear & Greed Index | None | Market sentiment |
| 8 | **Polymarket** | `clob.polymarket.com/` | Prediction market data | None documented | Prediction markets |
| 9 | **Messari (limited free)** | `api.messari.io/news/v1/news/feed` | Curated news with sentiment scores | 200 req/min | News intelligence |
| 10 | **Hypersub** | Web scrape | Token launch and subscription data | N/A | Token discovery |
| 11 | **SearXNG** | `hydra:8089/search` (self-hosted) | Privacy-respecting meta search engine | Unlimited | X web scraping, general search fallback |
| 12 | **FlareSolverr** | `talos:8191` (self-hosted) | Cloudflare bypass for scraping | Unlimited | When `web-extract` fails on Cloudflare sites |
| 13 | **PublicNode** | Public EVM RPC endpoints | Free EVM RPC access (Ethereum, Base, Arbitrum, etc.) | Rate-limited | EVM chain data fallback |
| 14 | **LlamaRPC** | `rpc.llamarpc.com` | Community EVM RPC | Rate-limited | EVM chain data fallback |
| 15 | **DRPC** | Public DRPC endpoints | Free tier EVM RPC + Solana RPC | Rate-limited | EVM + Solana chain data |
| 16 | **Solana Labs Public RPC** | `api.mainnet-beta.solana.com` | Solana public RPC | 100 req/10s per IP | Solana chain data fallback |
| 17 | **Lava Network** | Solana public endpoint | Solana RPC via Lava | Rate-limited | Solana chain data fallback |
| 18 | **ZAN** | Solana public endpoint | Solana RPC via ZAN | Rate-limited | Solana chain data fallback |
| 19 | **Etherscan/BSCscan/Arbiscan/etc** | `api.etherscan.io/api`, `api.bscscan.com/api`, etc. | Transaction data, contract ABIs, event logs | 5 req/s (no key basic, with key up to 100 req/s) | Transaction trace, contract verification |
| 20 | **CoinPaprika** | `api.coinpaprika.com/v1/` | Token prices, market data, exchanges | 100 req/min | Price data fallback |
| 21 | **CoinCap** | `api.coincap.io/v2/` | Real-time cryptocurrency prices | 200 req/min | Price data fallback |
| 22 | **CoinStats** | `openapiv1.coinstats.app/wallet/v1/balance/history/{addr}` | Wallet DeFi positions, balance history, cross-protocol | Unlimited basic endpoints | Wallet DeFi resolution |

---

## 3. FREEMIUM (Free Tier With API Key)

| # | Source | Endpoint(s) | What It Provides | Free Tier Limit | Key Env Var | Used In |
|---|--------|------------|-----------------|-----------------|-------------|---------|
| 1 | **Birdeye** | `public-api.birdeye.so/defi/token_overview`, `/price` | Token overview, real-time price, holder stats | 100 req/min (free key) | `BIRDEYE_API_KEY` | Token price, overview |
| 2 | **Solana Tracker** | `api.solanatracker.io` | Token price, metadata, trending, volume history | 5,000 req/mo (2 keys = 10K/mo) | `SOLANA_TRACKER_API_KEY_1/2` | Solana token pricing, trending, metadata |
| 3 | **Mobula** | `api.mobula.io/api/1/market/multi-data`, `/wallet/portfolio` | Long-tail DEX token pricing, wallet portfolio | 10,000 credits/mo | `MOBULA_API_KEY` | Long-tail token prices, wallet portfolios |
| 4 | **Messari** | `api.messari.io/news/v1/news/feed` | Curated news with per-asset sentiment scores | 200 req/min | `MESSARI_API_KEY` | News intelligence |
| 5 | **CryptoCompare/CoinDesk** | `min-api.cryptocompare.com/data/v2/news/` | Institutional-grade crypto news with categorization | Free tier with key | `COINDESK_API_KEY` | News aggregation |
| 6 | **VirusTotal** | `www.virustotal.com/api/v3/urls/{url_id}` | URL scan for phishing/malware detection | 500 req/day | `VIRUSTOTAL_API_KEY` | Token website security |
| 7 | **Santiment** | `api.santiment.net/graphql` | GitHub dev activity, social volume | 100 calls/day | `SANTIMENT_API_KEY` | Project dev health |
| 8 | **Dune Analytics** | `api.dune.com/api/v1/` | Custom on-chain queries, dashboards | 10K CU/mo | `DUNE_API_KEY` | Complex on-chain analysis |
| 9 | **Bitquery** | GraphQL endpoint (streaming) | DEX prices, holder distribution, transaction traces, cross-chain transfers, contract events | Free tier (limited credits) | `BITQUERY_API_KEY` | Blockchain data: prices, holders, trades, contract events |
| 10 | **GoPlus** | `api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}` | Token security checks: honeypot, tax, mint, ownership, LP lock | 10 req/s, no auth for basic | (optional key) | Token security scanning (37+ checks) |
| 11 | **Defender (OpenZeppelin)** | Defender API | Smart contract admin monitoring, upgrade detection | Free tier | `DEFENDER_API_KEY` | Contract upgrade monitoring |
| 12 | **GMGN** | GMGN API | Solana token metrics, trending, top traders | Free tier | `GMGN_API_KEY` | Solana token data |
| 13 | **0x** | `api.0x.org/swap/v1/price` | DEX swap prices, quotes | Free tier | `ZEROEX_API_KEY` | EVM token pricing |
| 14 | **Shyft** | Shyft API | Solana RPC, token balances, transaction history | Free tier (limited) | `SHYFT_API_KEY` | Solana wallet data |
| 15 | **Covalent** | `api.covalenthq.com/v1/` | Multi-chain balances, transactions, prices | Free tier | `COVALENT_API_KEY` | Multi-chain wallet data |
| 16 | **Trongrid** | `api.trongrid.io` | TRON blockchain data, token balances, transactions | 10 req/s | (public) | TRON chain data |
| 17 | **LunarCrush** | `api.lunarcrush.com/v2` | Social sentiment, social metrics, influencer data | Free tier | `LUNARCRUSH_API_KEY` | Social sentiment |
| 18 | **Reservoir** | `api.reservoir.tools` | NFT data, collections, orders, floor prices | Free tier | `RESERVOIR_API_KEY` | NFT data |

---

## 4. PAID (Premium API Keys)

| # | Source | What It Provides | Pricing | Key Env Var | Used In |
|---|--------|-----------------|---------|-------------|---------|
| 1 | **Arkham Intelligence** | Entity resolution (address → entity), counterparty graph, portfolio, labels, transfer history | Free trial month, then paid | `ARKHAM_API_KEY` | Entity intel, portfolio, labels, counterparty analysis |
| 2 | **Moralis** | Token balances, NFT holdings, transaction history, token prices, net worth, search | Paid (Web3 API) | `MORALIS_API_KEY` | Wallet tokens, NFTs, transactions, prices |
| 3 | **Alchemy** | Token balances, token metadata, NFT data, gas estimation, transaction simulation, RPC | Generous free tier, then pay-as-you-go | `ALCHEMY_API_KEY` | EVM token balances, metadata, RPC |
| 4 | **Helius** | Solana RPC (DAS API), webhooks, compressed NFTs, enhanced transactions, staking | Free tier (25K req/mo), then paid | `HELIUS_API_KEY` | Solana token data, NFTs, webhook alerts |
| 5 | **CoinGecko Pro** | Higher rate limits, on-chain data, GeckoTerminal API | $99+/mo | `COINGECKO_API_KEY` | Token prices, market data |
| 6 | **Etherscan Pro** | Higher rate limits, authenticated API | Paid tier | `ETHERSCAN_API_KEY` | Transaction data, contract verification |
| 7 | **Nansen (via Solscan free)** | Wallet labels via Solscan free tier (Nansen-labeled) | Free via Solscan | N/A (Read from Solscan) | Wallet labels (limited) |
| 8 | **Moralis MCP** | Enhanced Web3 data via Moralis MCP server | Via API key | `MORALIS_API_KEY` | Token data, wallet data |
| 9 | **Defender API** | Smart contract admin, upgrade detection | Free tier | `DEFENDER_API_KEY` | Contract monitoring |

---

## 5. MCP Servers (Self-Hosted / Remote)

| # | MCP Server | Type | What It Provides | Data Source |
|---|-----------|------|-----------------|-------------|
| 1 | `evm-direct` | Local (Node) | EVM direct node RPC calls | Public RPC nodes |
| 2 | `evmscope` | Local (Node) | EVM chain scope and analysis | Public RPC nodes |
| 3 | `jupiter-mcp` | Local (Node) | Solana Jupiter DEX quotes, swaps | Jupiter API |
| 4 | `crypto-feargreed-mcp` | Local (Python) | Fear & Greed Index | alternative.me |
| 5 | `crypto-indicators-mcp` | Local (Node) | Technical indicators | Calculated |
| 6 | `moralis-mcp` | Local (Node) | Moralis Web3 data | Moralis API |
| 7 | `web3-research-mcp` | Local (Node) | Web3 research queries | Multiple sources |
| 8 | `solana-mcp` | Local (Node) | Solana blockchain data | Solana RPC |
| 9 | **CoinGecko MCP** | Remote (HTTP) | CoinGecko API via MCP protocol | CoinGecko |
| 10 | **Birdeye MCP** | Remote (HTTP) | Birdeye token data | Birdeye |
| 11 | **Helius MCP** | Remote (HTTP) | Helius Solana data | Helius |
| 12 | **DexScreener MCP** | Remote (HTTP) | DexScreener pair/token data | DexScreener |
| 13 | **Etherscan MCP** | Remote (HTTP) | Etherscan explorer data | Etherscan |
| 14 | **DeFiLlama MCP** | Remote (HTTP) | DeFiLlama TVL, yields, prices | DeFiLlama |
| 15 | **Sourcify** | Remote (HTTP) | Smart contract verification, 4byte signatures | Sourcify |
| 16 | **QuickNode** | Remote (HTTP) | Blockchain RPC endpoints | QuickNode |
| 17 | **Blockscout** | Remote (HTTP) | EVM chain explorer data | Blockscout instances |
| 18 | **Dune** | Remote (HTTP) | On-chain analytics, dashboards | Dune Analytics |
| 19 | **Bitquery** | Remote (HTTP) | Multi-chain GraphQL data | Bitquery |
| 20 | **Defender** | Remote (HTTP) | Smart contract admin | OpenZeppelin |

---

## 6. Webhooks (Incoming Data Streams)

| # | Service | Events | Signature Verification | Used For |
|---|---------|--------|----------------------|----------|
| 1 | **Helius** | Transaction, token transfer, NFT sale | Helius HMAC signature | Real-time Solana alerts |
| 2 | **Arkham** | Entity label change, portfolio change | Arkham API key match | Entity tracking |
| 3 | **Moralis** | Token transfer, native balance, NFT transfer | Moralis webhook secret | EVM chain monitoring |
| 4 | **Alchemy** | Mined transaction, address activity, custom webhook | Alchemy signature | EVM chain monitoring |
| 5 | **Custom** | Arbitrary JSON payloads | User-defined HMAC | General webhook ingestion |

---

## 7. Social & News Sources

| # | Source | Type | What It Provides | Rate/Cost |
|---|--------|------|-----------------|-----------|
| 1 | **X/Twitter API** | Social API | User profiles, tweets, mentions, engagement metrics | Free tier: 10K reads/mo |
| 2 | **X Web Scraper** | Web Scrape | Tweet discovery, mentions, trending topics via SearXNG | Unlimited (free) |
| 3 | **CryptoPanic** | News API | Curated crypto news with sentiment voting | Free tier (unlimited) |
| 4 | **Messari News** | News API | Curated news with per-asset sentiment | 200 req/min |
| 5 | **CryptoCompare/CoinDesk** | News API | Institutional news with categorization | Free tier with key |
| 6 | **cryptocurrency.cv** | News API/RSS | Free unlimited crypto news, no API key | Unlimited |
| 7 | **arXiv** | Academic API | Crypto/blockchain academic papers | Unlimited |
| 8 | **Google News (RSS)** | RSS Feed | Crypto news aggregation | Unlimited |
| 9 | **Reddit (RSS)** | RSS Feed | Crypto subreddit discussions | Unlimited |
| 10 | **Telegram (scrape)** | Web Scrape | Scam channel monitoring, community sentiment | Unlimited |
| 11 | **Discord (scrape)** | Web Scrape | Scam pattern detection | Unlimited |
| 12 | **KOL Tracking** | Aggregated | 150+ key opinion leader performance tracking | Internal |

---

## 8. News Intelligence Pipeline (Combined)

30+ sources aggregated into one pipeline:

```
cryptocurrency.cv (REST + RSS)
├── Messari News (with sentiment)
├── CryptoCompare/CoinDesk
├── CryptoPanic (with community voting)
├── Google News RSS (crypto + blockchain topics)
├── Reddit RSS (r/cryptocurrency, r/ethfinance, r/solana)
├── arXiv API (academic papers)
├── X/Twitter (mentions of CryptoRugMunch + crypto KOLs)
├── Telegram (scam channel monitoring)
├── CoinDesk RSS
└── 20+ additional RSS feeds
    ↓
    Quality scoring → Deduplication → Category tagging → Sentiment analysis → Storage
```

---

## 9. DataBus Chain Summary

49 data types organized into provider chains in `app/domains/databus/providers/__init__.py`:

```
token_price:     Local > DexScreener > CoinGecko > Birdeye > Moralis
token_metadata:  Local > DexScreener > Birdeye > Alchemy
wallet_labels:   Local (190K) > Arkham > Nansen/Solscan
entity_intel:    Local > Arkham (paid)
holder_data:     Local > DexScreener > Bitquery
market_overview: Local > DeFiLlama > CoinGecko
news:            Local (30 sources) > Messari > CryptoCompare
alerts:          Local > Helius webhook > Arkham webhook
threat_check:    GoPlus > CryptoScamDB > Januus > VirusTotal
contract_scan:   Slither > Mythril > Sourcify
```

---

## 10. Data Freshness & Caching Strategy

| Data Type | Cache TTL | SWR Enabled | Warm Prefetch | Stale Fallback |
|-----------|-----------|-------------|---------------|----------------|
| token_price | 30s | Yes | Yes | Returns stale, refreshes in bg |
| token_metadata | 300s | Yes | Yes | Returns stale |
| wallet_labels | 3600s | Yes | Yes | Returns stale |
| entity_intel | 3600s | Yes | No (paid) | Returns stale |
| holder_data | 60s | Yes | Yes | Returns stale |
| market_overview | 120s | Yes | Yes | Returns stale |
| trending | 300s | Yes | Yes | Returns stale |
| news | 600s | No | Yes | N/A |
| alerts | 30s | Yes | Yes | Returns stale |
| threat_check | 300s | Yes | No | Returns stale |
| token_security | 300s | Yes | No | Returns stale |
| ohlcv | 60s | Yes | Yes | Returns stale |
| volume_authenticity | 300s | Yes | No | Returns stale |

---

## 11. Provider Health & Circuit Breakers

Each provider has:
- **Success/failure tracking** per request
- **Consecutive failure counter** — after 3 failures, circuit opens for 30s
- **Average latency tracking** (running weighted average)
- **Credit pressure detection** — when paid provider is at >80% monthly quota, free providers are preferred
- **Monthly quota enforcement** — hard cap on paid/freemium providers

All metrics available at: `GET /api/v1/databus/providers/health`

---

## 12. Data Quality Framework

Each intelligence output includes:

```python
{
    "data": { ... },
    "meta": {
        "source": "provider_name",
        "freshness": "2026-07-20T15:30:00Z",
        "cache_ttl": 300,
        "fallback_used": False,
        "confidence": 0.92,
        "schema_valid": True,
        "data_quality": {
            "score": 85,
            "level": "EXCELLENT"
        }
    }
}
```

**Quality dimensions scored:**
1. **Retrieval concentration** — how focused are top results? (25%)
2. **Similarity quality** — raw semantic match strength (20%)
3. **Source corroboration** — independent sources agreeing (20%)
4. **Reranker margin** — gap between top and runner-up (15%)
5. **Temporal freshness** — evidence age (10%)
6. **Entity exact-match bonus** — direct address/token match (10%)
