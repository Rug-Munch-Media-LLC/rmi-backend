---
license: other
license_name: proprietary
license_link: https://rugmunch.io/terms
tags:
  - x402
  - mcp
  - crypto-security
  - blockchain-intelligence
  - scam-detection
  - rug-pull-detector
  - wallet-analysis
  - whale-tracking
  - smart-money
  - defi
  - nft-analysis
  - honeypot-detector
  - ai-agents
  - model-context-protocol
  - fastapi
  - micropayments
  - usdc
  - usdt
  - bitcoin
  - solana
  - ethereum
  - base
  - arbitrum
  - optimism
  - polygon
  - bsc
  - avalanche
  - fantom
  - gnosis
  - tron
pipeline_tag: other
---

# 🛡️ Rug Munch Intelligence — x402 MCP Server

**70+ AI-powered crypto security & intelligence engines · 16 chains · x402 micropayments**

[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-6E40C9?logo=modelcontextprotocol&logoColor=white)](https://modelcontextprotocol.io)
[![x402 Protocol](https://img.shields.io/badge/Payment-x402-FF6900)](https://x402.org)
[![Tools](https://img.shields.io/badge/Tools-70%2B-00D4AA)](https://rugmunch.io)
[![Smithery](https://smithery.ai/badge/@cryptorugmuncher/rug-munch-intelligence)](https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence)

## What Is This?

Rug Munch Intelligence (RMI) is the most comprehensive crypto security & intelligence API server ever built. It exposes **70+ security engines** across **16 chains** via the **x402 micropayment protocol** and **Model Context Protocol (MCP)** — giving any AI agent, script, or application instant access to scam detection, whale tracking, wallet forensics, market analysis, and more.

> 🧠 **One endpoint. 70+ security engines. Zero API keys. Free trials on every tool.**

## Key Features

- **70+ security engines** across 12 categories: Security, Intelligence, Market, Analysis, Social, Launchpad, Premium, DeFi, NFT, Bundles, API, plus per-chain variants
- **16 chains** (14 EVM + Solana + Tron), with x402 payments on 13 payment rails including Bitcoin and SEPA/EUR
- **x402 v2 protocol**: Per-call micropayments ($0.01–$0.40) via 8 facilitators across USDC, USDT, BTC, and EUR
- **Free trials**: 1–5 calls per tool with no signup, gated by device fingerprint
- **MCP compatible**: Use with Claude Desktop, Cursor, Windsurf, ChatGPT, or any MCP client
- **6 discovery endpoints**: OpenAI, Anthropic, Gemini, LangChain, x402 discovery, and catalog formats

## Quick Start

### MCP Connection

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

### Raw HTTP

```bash
# List all 70+ security engines in OpenAI format
curl https://rugmunch.io/api/v1/x402-tools/openai-tools

# Free trial call — no payment needed
curl -X POST https://rugmunch.io/api/v1/x402-tools/rugshield \
  -H "Content-Type: application/json" \
  -d '{"address": "So11111111111111111111111111111111111111112", "chain": "solana"}'
```

## Tool Categories

| Category | Count | Top Tools |
|:---|:---:|:---|
| 🔐 Security + SENTINEL | 38 | `honeypot_check`, `rugshield`, `sentinel_scan`, `holder_analysis`, `flash_loan_detect` |
| 🧠 Intelligence | 27 | `whale`, `smartmoney`, `insider`, `cross_chain_whale`, `wallet_label_registry` |
| 📈 Market | 15 | `pulse`, `market_overview`, `funding_rate`, `options_flow`, `liquidation_heatmap` |
| 🔬 Analysis | 14 | `wallet`, `forensics`, `portfolio_tracker`, `correlation_matrix`, `drawdown_analyzer` |
| 🐦 Social | 11 | `sentiment`, `social_signal`, `meme_vibe_score`, `discord_alpha`, `telegram_pump_detect` |
| 🚀 Launchpad | 7 | `launch_intel`, `airdrop_finder`, `presale_scanner`, `ido_tracker`, `fair_launch_detect` |
| 💎 Premium | 7 | `forensic_valuation`, `deep_forensics`, `whale_network_map`, `full_wallet_dossier` |
| 💸 DeFi | 4 | `defi_yield_scanner`, `yield_aggregator`, `impermanent_loss`, `protocol_risk` |
| 🔄 Variants | 80 | Per-chain overrides for Solana, Base, Ethereum, BSC |

## Discovery Endpoints

All endpoints return the full 70+ security-engine catalog in their respective format:

| Endpoint | Format |
|:---|:---|
| `/api/v1/x402-tools/discovery` | x402 v2 protocol |
| `/api/v1/x402-tools/catalog` | Human-readable JSON |
| `/api/v1/x402-tools/openai-tools` | OpenAI function calling |
| `/api/v1/x402-tools/anthropic-tools` | Anthropic tool use |
| `/api/v1/x402-tools/gemini-tools` | Google Gemini declarations |
| `/api/v1/x402-tools/langchain-tools` | LangChain tool schema |

## SENTINEL Deep Scan

The SENTINEL suite provides 9 specialized security scanner modules that can run individually or as a full parallel scan:

| Module | Price | Description |
|:---|:---|:---|
| `holder_analysis` | $0.05 | HHI concentration, fake diversification |
| `bundle_detect` | $0.08 | Bundle/sniper detection |
| `exchange_fund_check` | $0.05 | CEX-funded wallet detection |
| `liquidity_verify` | $0.05 | Lock verification, fake locker detection |
| `dev_reputation` | $0.05 | Serial rugg detection |
| `wash_trading` | $0.08 | Circular transfer detection |
| `metadata_fingerprint` | $0.05 | HTML/description similarity |
| `pumpfun_analysis` | $0.08 | Bonding curve, bot detection (Solana) |
| `sentiment_check` | $0.05 | Social sentiment scoring |
| **`sentinel_scan`** | **$0.15** | **All 9 modules in parallel** |

## Payment Facilitators

8 facilitators across 13 payment rails:

1. 🪙 **Coinbase CDP** — Base, Solana, Ethereum, Polygon (USDC)
2. 🤖 **PayAI** — Base, Solana (USDC, deferred settlement)
3. ☁️ **Cloudflare x402** — Base (USDC)
4. ⚡ **EIP-7702** — Universal EVM (USDC)
5. 🔶 **TRON Self-Verify** — TRON (USDT/USDC/USDD)
6. 🟠 **Bitcoin Self-Verify** — Bitcoin (BTC)
7. 🌐 **AsterPay** — SEPA (EUR)
8. 🦀 **x402-rs** — Multi-chain (USDC)

## Links

- 🌐 Website: [rugmunch.io](https://rugmunch.io)
- 📖 Docs: [rugmunch.io/docs/mcp](https://rugmunch.io/docs/mcp)
- 🔗 MCP Endpoint: [rugmunch.io/mcp](https://rugmunch.io/mcp)
- 🔍 x402 Discovery: [rugmunch.io/.well-known/x402](https://rugmunch.io/.well-known/x402)
- 📦 GitHub: [github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp](https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp)
- 🛠️ Smithery: [smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence](https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence)

## License

Proprietary — © 2024–2026 Rug Munch Media LLC. All rights reserved.
Commercial use requires a license. See [rugmunch.io/terms](https://rugmunch.io/terms).
