# RMI x402 API Documentation

## Overview

RMI (Rug Munch Intelligence) provides 270+ crypto intelligence tools via x402 micropayment.
Every tool routes through our DataBus pipeline — 38 data chains, 67 providers, multi-layer caching.
If a primary data source fails, DataBus automatically falls back to the next provider in the chain.

**Base URL:** `https://mcp.rugmunch.io`

---

## Authentication & Payment

All paid endpoints require an `X-PAYMENT` header with a valid x402 payment receipt,
or an `X-WALLET` header for free trial calls.

### Payment Flow
1. Call any tool endpoint without payment → get `402 Payment Required`
2. Send USDC payment to the provided address on Base/Solana/Ethereum/etc.
3. Include payment receipt in `X-PAYMENT` header on retry

### Free Trials
Every tool has 1-5 free trial calls per wallet (24h reset):
- Basic tier: 3-5 free calls
- Premium tier: 1-2 free calls
- Elite tier: 0 free calls

Check remaining trials: `GET /api/v1/x402-databus/trials/{wallet_address}`

---

## Tool Tiers

| Tier | Price Range | Free Trials | Best For |
|------|-------------|-------------|----------|
| **Basic** | $0.01-$0.02 | 3-5 calls | Quick lookups, market data, prices |
| **Premium** | $0.05-$0.10 | 1-2 calls | Wallet intel, smart money, deep scans |
| **Elite** | $0.15-$0.25 | 0 calls | Institutional forensics, Arkham, Nansen |

---

## DataBus Direct Endpoints

These endpoints route directly through DataBus with automatic fallback chains.

### `GET /api/v1/x402-databus/catalog`
Full catalog of tools with pricing, tier access, and descriptions.

### `GET /api/v1/x402-databus/access-matrix`
Which data types each x402 tier can access.

### `GET /api/v1/x402-databus/trials/{wallet}`
Check remaining free trials for a wallet.

### `POST /api/v1/x402-databus/fetch`
Universal DataBus endpoint. Pass `data_type` + parameters.

**Request:**
```json
{
  "data_type": "token_price",
  "mint": "So11111111111111111111111111111111111111112",
  "chain": "solana"
}
```

### Basic Tier Tools ($0.01-$0.02)

| Endpoint | Data Type | Price | Description |
|----------|-----------|-------|-------------|
| `/token-price` | token_price | $0.01 | Get real-time token price |
| `/token-detail` | token_detail | $0.02 | Full token intelligence |
| `/trending` | trending | $0.01 | Trending tokens across chains |
| `/market-overview` | market_overview | $0.02 | Crypto market landscape |
| `/market-movers` | market_movers | $0.01 | Top gainers, losers, movers |
| `/tvl` | tvl | $0.01 | DeFi TVL data |
| `/news` | news | $0.01 | Crypto news feed |
| `/social-feed` | social_feed | $0.01 | Social sentiment feed |
| `/dex-data` | dex_data | $0.01 | DEX pool data |
| `/defi-protocols` | defi_protocols | $0.01 | DeFi protocol tracker |
| `/prediction-markets` | prediction_markets | $0.02 | Prediction market odds |
| `/prediction-signals` | prediction_signals | $0.02 | Trading signals |
| `/rugmap` | rugmap | $0.02 | Holder concentration map |
| `/rugmaps-analysis` | rugmaps_analysis | $0.02 | Holder distribution analysis |
| `/wallet-balance` | wallet_balance | $0.01 | Multi-chain wallet balance |
| `/wallet-labels` | wallet_labels | $0.02 | Wallet entity identification |
| `/risk-scan` | risk_scan | $0.02 | Quick rug risk scan |
| `/threat-check` | threat_check | $0.02 | Threat intelligence check |
| `/socialfi-resolve` | socialfi_resolve | $0.01 | Social identity resolver |

### Premium Tier Tools ($0.05-$0.10)

| Endpoint | Data Type | Price | Description |
|----------|-----------|-------|-------------|
| `/wallet-profile` | wallet_profile | $0.05 | Complete wallet profile |
| `/smart-money` | smart_money | $0.05 | Smart money tracker |
| `/gmgn-smart-money` | gmgn_smart_money | $0.05 | Smart money narratives |
| `/funding-source` | funding_source | $0.08 | Fund origin tracer |
| `/cross-chain` | cross_chain | $0.08 | Cross-chain activity |
| `/wallet-cluster` | wallet_cluster | $0.08 | Syndicate mapper |
| `/bundle-detect` | bundle_detect | $0.08 | Bot detector |
| `/wallet-tokens` | wallet_tokens | $0.05 | Token holdings breakdown |
| `/wallet-pnl` | wallet_pnl | $0.05 | Wallet profit & loss |
| `/contract-scan` | contract_scan | $0.08 | Deep contract audit |
| `/sentinel-deep` | sentinel_deep | $0.10 | Full threat scan |
| `/entity-intel` | entity_intel | $0.10 | Entity intelligence |
| `/arkham-labels` | arkham_labels | $0.10 | Institutional entity labels |
| `/arkham-entity` | arkham_entity | $0.10 | Entity resolution |
| `/rag-search` | rag_search | $0.05 | Knowledge search |

### Elite Tier Tools ($0.15-$0.25)

| Endpoint | Data Type | Price | Description |
|----------|-----------|-------|-------------|
| `/arkham-portfolio` | arkham_portfolio | $0.25 | Institutional portfolio intel |
| `/arkham-transfers` | arkham_transfers | $0.20 | Cross-chain transfer tracer |
| `/arkham-counterparties` | arkham_counterparties | $0.20 | Counterparty intelligence |
| `/nansen-labels` | nansen_labels | $0.15 | Smart money labels |
| `/nansen-smart-money` | nansen_smart_money | $0.15 | Top trader tracking |
| `/portfolio` | portfolio | $0.15 | Multi-wallet portfolio |

---

## Legacy x402-tools Endpoints

All `/api/v1/x402-tools/*` endpoints work identically. 230+ tools covering security,
intelligence, market, social, DeFi, and more. If a primary data source fails,
the DataBus fallback middleware automatically routes to the next available provider.

---

## MCP Server

`GET /mcp/tools` — Full tool catalog with MCP-compliant schemas (270+ tools)

`POST /mcp/call/{tool_id}` — Execute any tool via MCP protocol

`GET /mcp/capabilities` — Server capabilities and payment info

---

## Supported Chains

Base, Solana, Ethereum, BSC, Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis,
Tron, Bitcoin (12 chains for payments)

---

## Error Codes

| Code | Meaning | Action |
|------|---------|--------|
| 402 | Payment Required | Send x402 payment or use trial wallet |
| 400 | Bad Request | Check required parameters |
| 502 | No Data Available | All providers exhausted — retry later |
| 429 | Rate Limited | Wait and retry |

---

## DataBus Architecture

Every data request flows through our 38-chain pipeline:

```
Request → Primary Provider → (fail) → Fallback Provider → (fail) → Cache → Response
                                        ↓ success
                                    Cache + Respond
```

- **Local cache**: Sub-second response for repeated queries (Redis-backed)
- **Multi-provider fallback**: Each data type has 2-5 providers ranked by reliability
- **Consumer-aware access control**: x402_free/basic/premium/enterprise tiers control data depth
- **Zero source names in responses**: Clients never see which provider supplied data
