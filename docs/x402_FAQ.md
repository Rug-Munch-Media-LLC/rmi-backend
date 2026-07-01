# RMI x402 Platform — Complete FAQ

## General

**What is Rug Munch Intelligence (RMI)?**
A unified crypto intelligence platform with 270+ tools for token security, wallet forensics, whale tracking, market data, and blockchain queries. Every tool routes through our DataBus pipeline with 38 data chains and 67 providers for maximum reliability.

**How many tools are free?**
Every paid tool includes 1-5 free trial calls. Connecting a wallet unlocks full trial allotment. Basic tools get 3-5 trials, premium tools get 1-2, elite tools have no free tier.

**What chains are supported?**
Base, Solana, Ethereum, BSC, Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis, TRON, Bitcoin (12 chains for payments; 38 for data queries).

## Pricing & Payments

**How does x402 pricing work?**
You pay per tool call in USDC. Prices range from $0.01 (basic lookups) to $0.25 (institutional forensics). Payment happens automatically via the x402 protocol — no accounts, no subscriptions, no prepayment.

**For Bots (x402 protocol):**
1. Call any paid tool endpoint → get `402 Payment Required` response
2. Send USDC payment to the provided address on any supported chain
3. Include payment receipt in `X-PAYMENT` header on retry
4. For trials: include `X-Device-Id` header for 1 free trial, `X-WALLET` header for full allotment

**For Humans (wallet connect):**
1. Connect wallet via MetaMask, Phantom, or WalletConnect
2. Send USDC payment directly
3. Include `X-WALLET` header for full trial allotment (3-5 free calls per tool)

**Which chains can I pay on?**
Base, Solana, Ethereum, BSC, TRON, Bitcoin, Polygon, Arbitrum, Optimism, Avalanche, Fantom, Gnosis. Base and Solana offer instant settlement.

**What if a paid tool returns no data?**
Full automatic refund within 48 hours via POST `/api/v1/x402/refund`.

**Do free trials reset?**
Yes — every 24 hours. Anti-abuse fingerprinting ensures fair usage. Bot fingerprints get 1 trial per tool; connected wallets get the full allotment.

## DataBus Architecture

**What is DataBus?**
Our single-source data pipeline. Every tool — whether bot or human — routes through DataBus. It provides:
- **38 data chains** with automatic provider failover
- **67 providers** ranked by reliability (local → free → paid)
- **Multi-layer caching** (memory → Redis → R2 cold storage)
- **6-tier access control** (public, authenticated, basic, premium, admin, x402_paid)

**What happens when a data source fails?**
DataBus automatically falls back to the next provider in the chain. For example, token_price tries: local cache → Jupiter → DexScreener → Binance → CoinGecko. You always get data.

**Do I need to specify which provider to use?**
No. DataBus handles provider selection automatically. You specify WHAT data you want, not WHERE it comes from.

## DataBus Direct Endpoints (40 tools)

### Basic Tier ($0.01-$0.02)
| Endpoint | Price | Description |
|----------|-------|-------------|
| POST /token-price | $0.01 | Real-time token price |
| POST /token-detail | $0.02 | Full token intelligence |
| POST /trending | $0.01 | Trending tokens feed |
| POST /market-overview | $0.02 | Market landscape |
| POST /market-movers | $0.01 | Top gainers/losers |
| POST /tvl | $0.01 | DeFi TVL data |
| POST /news | $0.01 | Crypto news feed |
| POST /social-feed | $0.01 | Social sentiment |
| POST /dex-data | $0.01 | DEX pool data |
| POST /defi-protocols | $0.01 | Protocol tracker |
| POST /prediction-markets | $0.02 | Prediction market odds |
| POST /prediction-signals | $0.02 | Trading signals |
| POST /bubble-map | $0.02 | Holder concentration map |
| POST /rugmaps-analysis | $0.02 | Holder distribution |
| POST /wallet-balance | $0.01 | Multi-chain wallet balance |
| POST /wallet-labels | $0.02 | Wallet entity identification |
| POST /risk-scan | $0.02 | Rug risk scan |
| POST /threat-check | $0.02 | Threat intelligence |
| POST /socialfi-resolve | $0.01 | Social identity resolver |

### Premium Tier ($0.05-$0.10)
| Endpoint | Price | Description |
|----------|-------|-------------|
| POST /wallet-profile | $0.05 | Complete wallet profile |
| POST /smart-money | $0.05 | Smart money tracker |
| POST /gmgn-smart-money | $0.05 | Smart money narratives |
| POST /funding-source | $0.08 | Fund origin tracer |
| POST /cross-chain | $0.08 | Cross-chain activity |
| POST /wallet-cluster | $0.08 | Syndicate mapper |
| POST /bundle-detect | $0.08 | Bot activity detector |
| POST /wallet-tokens | $0.05 | Token holdings breakdown |
| POST /wallet-pnl | $0.05 | Wallet PnL |
| POST /contract-scan | $0.08 | Deep contract audit |
| POST /sentinel-deep | $0.10 | Full threat scan |
| POST /entity-intel | $0.10 | Entity intelligence |
| POST /arkham-labels | $0.10 | Institutional entity labels |
| POST /arkham-entity | $0.10 | Entity resolution |
| POST /rag-search | $0.05 | Knowledge search |

### Elite Tier ($0.15-$0.25)
| Endpoint | Price | Description |
|----------|-------|-------------|
| POST /arkham-portfolio | $0.25 | Institutional portfolio |
| POST /arkham-transfers | $0.20 | Cross-chain transfers |
| POST /arkham-counterparties | $0.20 | Counterparty intelligence |
| POST /nansen-labels | $0.15 | Smart money labels |
| POST /nansen-smart-money | $0.15 | Top trader tracking |
| POST /portfolio | $0.15 | Multi-wallet portfolio |

### Universal Endpoints
| Endpoint | Description |
|----------|-------------|
| POST /fetch | Universal DataBus fetch (any data_type) |
| GET /catalog | Full tool catalog with pricing |
| GET /access-matrix | Data type access by tier |
| GET /trials/{identifier} | Trial status for wallet/fingerprint |

## MCP Server

**GET /mcp/tools** — Full catalog with MCP-compliant schemas (270+ tools)
**POST /mcp/call/{tool_id}** — Execute any tool via MCP protocol
**GET /mcp/capabilities** — Server capabilities and payment info
**GET /.well-known/x402** — x402 discovery document (12 chains, 8 facilitators)

## Security & Privacy

**Is my data safe?**
- All API keys are stored in an encrypted vault (GPG pass store), never in .env
- No source provider names are exposed in responses
- Wallet addresses are hashed for trial tracking
- Fingerprinting is VPN-resistant (canvas/WebGL/font, not just IP)
- 24-hour trial reset with per-tool limits

**What access control is used?**
6 consumer types with granular data packaging:
- **public_web**: Summary data only, no raw internals
- **authenticated**: Summary for basic, denied for premium
- **premium**: Full data for most types, summary for Arkham
- **admin**: Full access including raw data
- **mcp_tool**: Scoped per tool_id permissions
- **x402_paid**: Scoped per pricing tier (free/basic/premium/enterprise)
