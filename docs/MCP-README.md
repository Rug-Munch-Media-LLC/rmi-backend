# Rug Munch Intelligence — MCP Server Documentation

> **70+ crypto security engines · 16 chains · x402 micropayments · Free trials**

[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-6E40C9?logo=modelcontextprotocol&logoColor=white)](https://modelcontextprotocol.io)
[![Smithery](https://img.shields.io/badge/Listed-Smithery-FF6900)](https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence)
[![Glama](https://img.shields.io/badge/Listed-Glama-7C3AED)](https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence)

---

## Overview

Rug Munch Intelligence (RMI) is a crypto security and market intelligence MCP server. It provides **70+ security engines** across **16 chains** for scam detection, rug pull prevention, wallet forensics, whale tracking, contract auditing, and market analysis — accessible via the Model Context Protocol.

Every tool offers **1–5 free trial calls** (gated by device fingerprint) and **x402 micropayments** ($0.01–$0.40/call) via 8 payment facilitators across 13 payment rails.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  AI Agent (Claude, Cursor, GPT, Windsurf, etc.)       │
│  Uses MCP protocol to discover and call tools          │
└───────────────────────┬─────────────────────────────────┘
                        │ MCP (HTTP/SSE)
                        ▼
┌─────────────────────────────────────────────────────────┐
│  rugmunch.io/mcp                                       │
│  MCP Server — 70+ engines, schema discovery            │
├─────────────────────────────────────────────────────────┤
│  x402 Payment Gatekeeper                               │
│  Trial enforcement → Payment required → 402 response  │
│  8 facilitators × 13 payment rails × USDC/USDT/BTC/EUR│
├─────────────────────────────────────────────────────────┤
│  FastAPI Backend — 80+ modules                         │
│  Security · Intelligence · Market · Social · Forensics │
├─────────────────────────────────────────────────────────┤
│  Data Layer                                             │
│  DexScreener · CoinGecko · Helius · Birdeye · Jupiter  │
│  Etherscan · Moralis · DefiLlama · 28 providers        │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Claude Desktop

```json
{
  "mcpServers": {
    "rug-munch-intelligence": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://rugmunch.io/mcp"],
      "env": {}
    }
  }
}
```

### Cursor / Windsurf

```json
{
  "mcpServers": {
    "rug-munch-intelligence": {
      "url": "https://rugmunch.io/mcp",
      "transport": "http"
    }
  }
}
```

### Raw HTTP (cURL)

```bash
# Discover available tools
curl https://rugmunch.io/api/v1/x402-tools/discovery

# Call a tool (free trial — no payment needed)
curl -X POST https://rugmunch.io/api/v1/x402-tools/rugshield \
  -H "Content-Type: application/json" \
  -H "X-Client-ID: my-fingerprint-id" \
  -d '{"address": "So11111111111111111111111111111111111111112", "chain": "solana"}'

# Call a paid tool (with x402 payment)
curl -X POST https://rugmunch.io/api/v1/x402-tools/audit \
  -H "Content-Type: application/json" \
  -H "x-pay: <x402-payment-header>" \
  -d '{"address": "0x...", "chain": "ethereum"}'
```

### Python

```python
import requests

# Discover
tools = requests.get("https://rugmunch.io/api/v1/x402-tools/discovery").json()
print(f"{tools['total_tools']} tools available")

# Free trial call
result = requests.post(
    "https://rugmunch.io/api/v1/x402-tools/rugshield",
    headers={"X-Client-ID": "my-app"},
    json={"address": "So11111111111111111111111111111111111111112", "chain": "solana"}
).json()
```

---

## Tool Categories

| Category | Count | Highlight Tools |
|----------|-------|----------------|
| 🔒 Security | 29 + 9 SENTINEL | `audit`, `rugshield`, `honeypot_check`, `sentinel_scan`, `holder_analysis`, `flash_loan_detect`, `governance_attack` |
| 🧠 Intelligence | 27 | `smartmoney`, `whale_scan`, `cluster`, `insider`, `cross_chain_whale`, `degen_score`, `wallet_label_registry` |
| 📊 Market | 15 | `pulse`, `market_overview`, `chain_health`, `funding_rate`, `options_flow`, `liquidation_heatmap`, `volatility_surface` |
| 🔬 Analysis | 14 | `wallet`, `wallet_pnl`, `portfolio_tracker`, `forensics`, `correlation_matrix`, `drawdown_analyzer`, `volume_profile` |
| 💬 Social | 11 | `sentiment`, `social_signal`, `tw_profile`, `meme_vibe_score`, `telegram_pump_detect`, `discord_alpha`, `reddit_sentiment` |
| 🚀 Launch | 7 | `launch`, `launch_intel`, `airdrop_finder`, `presale_scanner`, `ido_tracker`, `fair_launch_detect` |
| 🔎 Premium | 7 | `forensic_valuation`, `osint_identity_hunt`, `deep_forensics`, `whale_network_map`, `cross_chain_trace`, `full_wallet_dossier` |
| 💎 DeFi | 4 | `defi_yield_scanner`, `yield_aggregator`, `impermanent_loss`, `protocol_risk` |
| 🖼 NFT | 2 | `nft_wash_detector`, `nft_floor_analytics` |
| 📦 Bundle | 4 | `security_pack`, `intelligence_pack`, `all_in_one`, `forensic_pack` |
| 🔌 API | 3 | `catalog`, `mcp-proxy`, `human-execute` |
| 🔄 Variant | 80 | Per-chain variants for Solana, Base, Ethereum, BSC |

**Total: 70+ security engines**

---

## Supported Blockchains

| Chain | Chain ID | USDC | USDT | BTC | EUR |
|-------|----------|------|------|-----|-----|
| Base | 8453 | ✅ | — | — | — |
| Solana | — | ✅ | — | — | — |
| Ethereum | 1 | ✅ | — | — | — |
| BSC | 56 | ✅ | ✅ | — | — |
| Arbitrum | 42161 | ✅ | — | — | — |
| Optimism | 10 | ✅ | — | — | — |
| Polygon | 137 | ✅ | — | — | — |
| Avalanche | 43114 | ✅ | — | — | — |
| Fantom | 250 | ✅ | — | — | — |
| Gnosis | 100 | ✅ | — | — | — |
| TRON | — | ✅ | ✅ | — | — |
| Bitcoin | — | — | — | ✅ | — |
| SEPA (EUR) | — | — | — | — | ✅ |

---

## Payment Facilitators

| Facilitator | Chains | Asset | Fee | Description |
|------------|--------|-------|-----|-------------|
| Coinbase CDP | Base, Solana | USDC | Free | Fee-free via Coinbase Developer Platform |
| PayAI | Base, Solana | USDC | Variable | Deferred settlement |
| Cloudflare x402 | Base Sepolia, Ethereum | USDC | Low | Cloudflare-managed facilitation |
| EIP-7702 | BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base | USDC | Low | Universal EVM via EIP-7702 authorization |
| TRON Self-Verify | TRON | USDT/USDC/USDD | Free | Self-verified via TronGrid API |
| Bitcoin Self-Verify | Bitcoin | BTC | Free | Self-verified via Mempool.space (1-conf) |
| AsterPay | SEPA | EUR | Variable | European bank transfer |
| x402-rs | Multi-chain | USDC | Low | Self-hosted x402-rs Docker |

---

## Pricing & Trials

| Tier | Cost | Description |
|------|------|-------------|
| **Free Trial** | $0 | 1–5 calls per tool. Device fingerprint-gated. No wallet needed. |
| **Wallet Connected** | $0 | Connect wallet for 3 free calls per standard tool, 1 per premium. |
| **Pay Per Call** | $0.01–$0.40 | x402 micropayment per call. Auto-handled by MCP client. |

### Refund Policy
- **Full refund** if a tool returns no data
- Request within 48 hours via `POST /api/v1/x402/refund` with transaction hash
- No questions asked on empty-result refunds

---

## Authentication Hierarchy

1. **No auth** → Trial mode (1–5 free calls, fingerprint-gated)
2. **Wallet signature** → Extended trials (3 per tool, wallet-gated)
3. **x402 payment** → Full access (micropayment per call)
4. **API key** → Subscription access (coming soon)

---

## Error Handling

| HTTP Code | Meaning | Action |
|-----------|---------|--------|
| 200 | Success | Use response data |
| 402 | Payment Required | Send x402 payment header and retry |
| 429 | Rate Limited | Wait and retry with backoff |
| 404 | Tool Not Found | Check tool ID via `/discovery` |
| 500 | Server Error | Retry after brief delay |

---

## Rate Limits

- **Trial users**: 60 requests/hour per fingerprint
- **Paid users**: 300 requests/hour per wallet
- **Enterprise**: Custom limits — contact mcp@rugmunch.io

---

## Links & Resources

| Resource | URL |
|----------|-----|
| Website | https://rugmunch.io |
| Documentation | https://rugmunch.io/docs/mcp |
| MCP Endpoint | https://rugmunch.io/mcp |
| Discovery | https://rugmunch.io/.well-known/mcp.json |
| x402 Discovery | https://rugmunch.io/.well-known/x402 |
| GitHub | https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp |
| Smithery | https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence |
| Glama | https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence |

---

## License

Proprietary — © 2024–2026 Rug Munch Media LLC. All rights reserved.
