# RMI Platform — System Architecture

## Overview

Rug Munch Intelligence is a unified crypto intelligence platform serving 234 tools across 9 provider categories. Every data call routes through a multi-layer caching shield with automatic provider fallback. The x402 micropayment layer handles paid tool access across 8 chains with instant settlement.

```
                            ┌─────────────────────┐
                            │   FRONTEND / BOTS   │
                            │  Web | Telegram | API│
                            └──────────┬──────────┘
                                       │
                  ┌────────────────────┼────────────────────┐
                  │                    │                    │
          ┌───────▼───────┐   ┌───────▼───────┐   ┌───────▼───────┐
          │  MCP ENDPOINT │   │  REST API     │   │  X402 GATEWAY │
          │  /mcp/*       │   │  /api/v1/*    │   │  CF Workers   │
          └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
                  │                    │                    │
                  └────────────────────┼────────────────────┘
                                       │
                          ┌────────────▼────────────┐
                          │    CACHING SHIELD       │
                          │  L1 Memory → Rate Limit │
                          │  → Provider Chain       │
                          └────────────┬────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
    ┌─────────▼─────────┐  ┌──────────▼──────────┐  ┌─────────▼─────────┐
    │  DATA PROVIDERS   │  │  LOCAL MCP SERVERS  │  │  SERVICE MCP      │
    │  20 sources       │  │  SVM (60) + EVM (25)│  │  GMGN, Birdeye... │
    └───────────────────┘  └─────────────────────┘  └───────────────────┘
```

## Tool Categories

| Category | Count | Access | Description |
|----------|-------|--------|-------------|
| Security Scanning | 45 | Free trial + paid | Rug pulls, honeypots, audits, clone detection |
| Wallet Intelligence | 38 | Free trial + paid | PnL, clustering, insider networks, whale tracking |
| Market Data | 32 | Free trial + paid | Prices, liquidity, volume, arbitrage, trends |
| Token Analytics | 28 | Free trial + paid | Holder distribution, sniper detection, deployer history |
| DeFi Analytics | 24 | Free trial + paid | TVL, yields, protocol risk, liquidity flow |
| Social Signals | 18 | Free trial + paid | Sentiment, KOL tracking, profile flips |
| **Caching Shield** | **13** | **Free internal** | Funding trace, risk scan, token price, wallet balance |
| Local MCP | 85 | Self-hosted | SVM Solana RPC, EVM blockchain queries |
| Free Public MCP | 50 | Free external | Boar blockchain (ETH, ENS, contracts) |

## Caching Shield Architecture

Every data call goes through three layers before hitting any external API:

1. **L1 Memory Cache** — Sub-millisecond lookup. TTLs: 8s (price) to 1hr (contract ABI).
2. **Rate Limiter** — Token bucket per provider. Prevents burning free tier quotas.
3. **Provider Chain** — Ordered fallback. If primary fails, next provider tried automatically.

Example: Token Price Request
```
Request → L1 cache (miss) → Rate check (allowed) → Jupiter (primary) →
  if fail: Solana Tracker → if fail: DexScreener → if fail: Binance
Result cached for 8 seconds. Next request hits L1 cache instantly.
```

## Provider Fallback Chains

| Data Type | Primary | Fallback 1 | Fallback 2 | Fallback 3 |
|-----------|---------|------------|------------|------------|
| Token Price | Jupiter | Solana Tracker | DexScreener | Binance |
| Token Metadata | Helius DAS | Solana Tracker | Jupiter | DexScreener |
| Wallet Balance | Helius | QuickNode | Alchemy | PublicNode |
| Risk Scan | GoPlus | RugCheck | Honeypot | Local Labels |
| EVM Funding | Blockscout | Etherscan | Public RPC | Boar MCP |
| Solana Funding | Helius | Solana Tracker | Public RPC | Local Labels |

## Free Access Tiers

### Free Trials (x402 Tools)
- Every paid tool includes 1-5 free trial calls
- No payment required for trial calls
- Fingerprint-gated anti-abuse protection
- Reset monthly or on tool updates

### Always-Free Tools
- **Funding Tracer** — Trace wallet funding sources (Solana + 9 EVM chains)
- **Token Price** — Real-time price with multi-provider consensus
- **Risk Scan** — Quick security check with 4 fallback layers
- **Wallet Balance** — Balance lookup with provider redundancy
- **GMGN Security** — Token safety analysis
- **Etherscan Gas** — Current gas prices
- **CoinGecko Price** — Market data
- **Langfuse Stats** — Observability metrics
- **All Local MCP Tools** — 85 tools running on our infrastructure

### Free Tier Limits (External APIs)
Solana Tracker: 5,000 requests/month across 2 accounts (6 RPS combined)
Helius: 2 accounts, 25 RPS each (50 RPS total)
Blockscout PRO: 100,000 credits/day, 5 RPS
CoinMarketCap: 10,000 calls/month, 11 endpoints
Boar MCP: Unlimited (keyless, read-only)

## X402 Payment System

- **Price range:** $0.01 - $0.40 per tool call
- **Payment chains:** Base, Solana, Ethereum, BSC, TRON, Bitcoin, Polygon, Arbitrum
- **8 payment facilitators** with automatic fallback
- **Instant refund** if tool returns no data
- **Settlement:** Instant on Base and Solana

## Local MCP Servers

Two self-hosted MCP servers run on our bare metal, accessed via stdio transport:

**Solana SVM MCP** (60 tools, Rust)
- getBalance, getAccountInfo, getTokenSupply, getSignaturesForAddress
- getTransaction, getProgramAccounts, getBlock, getBlockHeight
- WebSocket subscriptions for real-time updates
- Built-in x402 payment protocol support

**EVM MCP** (25 tools, 86 networks)
- getBalance, getTokenBalance, getAllowance
- getTransaction, getTransactionReceipt, waitForTransaction
- getBlock, resolveENS, lookupENS
- getGasPrice, getContractABI, getChainInfo

## Observability

Langfuse cloud with smart sampling:
- 20% of normal traces sent to cloud
- 100% of errors sent to cloud
- Full archive in local ClickHouse
- Target: 30,000 observations/month (free tier: 50,000)

## Deployment

- **Server:** Bare metal VPS (193GB SSD, Ubuntu)
- **Containers:** Docker Compose (30 containers)
- **CI/CD:** GitHub Actions — auto-deploy on push to main
- **Edge:** Cloudflare Workers for x402 payment gateway
- **Secrets:** GPG-encrypted vault (52 secrets), age-encrypted Docker runtime

## Auto-Updating Platform Manifest

All platform descriptions, tool counts, pricing, and agent skills are managed by a single source of truth at `app/caching_shield/platform_manifest.py`. Every external surface reads from here.

**Live endpoints (always current):**
- `GET /mcp/manifest` — Full platform manifest with live tool counts
- `GET /mcp/membership` — Membership tiers, scan packs, streams, research, batch
- `GET /mcp/skills` — 18 agent skills with workflows and anti-abuse rules

**Directory sync (run to update external listings):**
```bash
python3 /root/backend/scripts/sync_platforms.py
```
Updates Smithery, Glama, mcp.so, and README from the manifest.

**Tools by category (auto-counted):**
| Category | Count | Access |
|----------|-------|--------|
| Paid Tools (with free trials) | 200+ | 1-5 free calls, then pay-per-use |
| Local MCP Tools | 85 | Self-hosted, no rate limits |
| Free Public MCP | 50 | Keyless, read-only |
| Agent Skills | 18 | Free, included with any access |
| Scan Packs | 4 | 50-53% off individual tools |
| Membership Tiers | 4 | $4.99-$199.99/mo, daily call limits |
| Streaming Feeds | 4 | Real-time data via WebSocket |
| Research Reports | 4 | Deep dive investigations |
| Batch Products | 3 | 75-90% off bulk scanning |
| AI Data Feeds | 3 | Machine-ready market data |
