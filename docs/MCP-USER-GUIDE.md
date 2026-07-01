# Rug Munch Intelligence — User Guide

> **From zero to crypto intelligence in 5 minutes.** This guide walks you through connecting your wallet, running your first scan, and mastering all 210 tools.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Connecting Your Wallet](#2-connecting-your-wallet)
3. [Running Your First Scan](#3-running-your-first-scan)
4. [Understanding Results](#4-understanding-results)
5. [Tool Categories](#5-tool-categories)
6. [Payment Options](#6-payment-options)
7. [Choosing a Facilitator](#7-choosing-a-facilitator)
8. [Managing Trials & Credits](#8-managing-trials--credits)
9. [Advanced Usage](#9-advanced-usage)
10. [Refunds & Support](#10-refunds--support)

---

## 1. Getting Started

### For AI Agent Users (Claude, Cursor, Windsurf)

1. Open your MCP configuration file
2. Add the RMI server connection
3. Restart your AI assistant
4. Start asking crypto security questions

```json
{
  "mcpServers": {
    "rug-munch-intelligence": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://rugmunch.io/mcp"]
    }
  }
}
```

✅ **That's it!** Your AI now has 210 crypto intelligence tools.

### For Human Users (Web Platform)

1. Visit [rugmunch.io](https://rugmunch.io)
2. Click **Sign In** or connect your wallet
3. Navigate to any tool page (Scanner, Intelligence, Markets)
4. Enter a token address or wallet and hit Scan

### For Developers (API)

```bash
# Discover all tools
curl https://rugmunch.io/api/v1/x402-tools/discovery

# Call a free-trial tool
curl -X POST https://rugmunch.io/api/v1/x402-tools/rugshield \
  -H "Content-Type: application/json" \
  -H "X-Client-ID: my-app-v1" \
  -d '{"address": "So11111111111111111111111111111111111111112", "chain": "solana"}'
```

---

## 2. Connecting Your Wallet

Connecting a wallet gives you **extended free trials** and enables **x402 micropayments** for paid calls.

### Supported Wallets

| Wallet | Chains | How to Connect |
|--------|--------|---------------|
| **MetaMask** | All EVM chains | Click "Connect Wallet" → Select MetaMask → Approve |
| **Phantom** | Solana + EVM | Click "Connect Wallet" → Select Phantom → Approve |
| **Solflare** | Solana | Click "Connect Wallet" → Select Solflare → Approve |
| **Backpack** | Solana | Click "Connect Wallet" → Select Backpack → Approve |
| **Coinbase Wallet** | Base, Ethereum | Click "Connect Wallet" → Select Coinbase → Approve |
| **Rainbow** | All EVM | Click "Connect Wallet" → Select Rainbow → Approve |
| **TronLink** | TRON | Click "Connect Wallet" → Select TronLink → Approve |
| **WalletConnect** | Any | Click "Connect Wallet" → Scan QR code |

### What happens when I connect?

1. Your wallet address is stored (we never request private keys)
2. You receive **3 free calls per standard tool**, **1 per premium tool**
3. For paid calls, you'll sign an EIP-3009 or EIP-712 authorization (no gas fees!)
4. Payment is settled on-chain by our facilitators

⚠️ **We never have access to your funds.** x402 authorizations are specific to the exact amount for each call.

---

## 3. Running Your First Scan

### Quick Security Check

The fastest way to assess a token is `rugshield`:

```
POST /api/v1/x402-tools/rugshield
{
  "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1o",
  "chain": "solana"
}
```

**Response includes:**
- Overall safety score (0–100)
- Category breakdown (liquidity, ownership, holders, contract)
- Red flags detected
- Risk level: Low / Medium / High / Critical

### Deep Contract Audit

For detailed vulnerability analysis:

```
POST /api/v1/x402-tools/audit
{
  "address": "0x...",
  "chain": "ethereum"
}
```

**Returns:** Slither analysis, proxy detection, ownership structure, reentrancy risks, access control assessment.

### Whale Tracking

See what smart money is doing:

```
POST /api/v1/x402-tools/whale_scan
{
  "chain": "ethereum",
  "min_value": 100000
}
```

---

## 4. Understanding Results

### Security Scores

| Score | Risk Level | Action |
|-------|-----------|--------|
| 80–100 | ✅ Low | Safe to interact with caution |
| 60–79 | ⚠️ Medium | Research further before investing |
| 40–59 | 🔴 High | Significant risk — proceed with extreme caution |
| 0–39 | 💀 Critical | Likely scam — do not interact |

### Risk Categories

- **Liquidity Risk**: Is liquidity locked? Can it be removed?
- **Ownership Risk**: Is ownership renounced? Can contract be upgraded?
- **Holder Risk**: Is supply concentrated in few wallets?
- **Contract Risk**: Are there hidden mint functions? Transfer restrictions?
- **Social Risk**: Are social signals aligned with on-chain data?

---

## 5. Tool Categories

| Category | Tools | Best For |
|----------|-------|----------|
| 🔒 Security | 38 (29 + 9 SENTINEL) | Pre-investment safety checks, deep token scanning |
| 🧠 Intelligence | 27 | Smart money, whale, insider tracking |
| 📊 Market | 15 | Price, trends, arbitrage, yields, options |
| 🔬 Analysis | 14 | Wallet forensics, portfolio, correlation, drawdown |
| 💬 Social | 11 | Sentiment, Twitter, Discord, Telegram, Reddit |
| 🚀 Launch | 7 | New token discovery, presale, IDO tracking |
| 🔎 Premium | 7 | Institutional-grade forensics, OSINT |
| 💎 DeFi | 4 | Yield scanning, aggregator, impermanent loss |
| 🖼 NFT | 2 | Wash trading detection, floor analytics |

Browse all 210 tools at [rugmunch.io/tools](https://rugmunch.io/tools)

---

## 6. Payment Options

### Free Trials (No Wallet Needed)

Every tool offers 1–5 free calls. No sign-up required. Trials reset per device fingerprint.

### x402 Micropayments (Per Call)

| Tier | Cost | Access |
|------|------|--------|
| Free Trial | $0 | 1–5 calls/tool, fingerprint-gated |
| Wallet Connected | $0 | 3 calls/tool (standard), 1/premium |
| Pay Per Call | $0.01–$0.40 | Unlimited, x402 micropayment |

### Accepted Currencies

- **USDC**: Base, Solana, Ethereum, BSC, Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis
- **USDT/USDD**: TRON
- **BTC**: Bitcoin (1-confirmation)
- **EUR**: SEPA transfer via AsterPay

---

## 7. Choosing a Facilitator

| Facilitator | Chains | Asset | Fee | Best For |
|------------|--------|-------|-----|----------|
| Coinbase CDP | Base, Solana | USDC | Free | Everyday use, lowest cost |
| PayAI | Base, Solana | USDC | Variable | Deferred settlement |
| EIP-7702 | All EVM | USDC | Low | Any EVM chain |
| TRON Self-Verify | TRON | USDT/USDC/USDD | Free | TRON users |
| Bitcoin Self-Verify | Bitcoin | BTC | Free | BTC holders |
| AsterPay | SEPA | EUR | Variable | European users |
| x402-rs | Multi-chain | USDC | Low | Self-hosted |

**Recommendation**: Use **Coinbase CDP** for Base/Solana USDC (fee-free). For TRON, use TRON Self-Verify. For BTC, use Bitcoin Self-Verify. For European bank transfers, use AsterPay.

---

## 8. Managing Trials & Credits

### Check Your Trial Balance

```bash
# Via fingerprint
curl https://rugmunch.io/api/v1/x402-tools/trials?fingerprint=<your-id>

# Via wallet address
curl https://rugmunch.io/api/v1/x402-tools/trials?wallet=<your-address>
```

### Understanding Trial Refresh

- **Device fingerprint** trials: 1–5 per tool, refresh monthly
- **Wallet-connected** trials: 3 per standard, 1 per premium, refresh monthly
- **Paid calls**: No limit while funds are available

---

## 9. Advanced Usage

### Bundle Calls

Combine multiple tools for comprehensive analysis:

```
POST /api/v1/x402-tools/unified_scan
{
  "address": "0x...",
  "chain": "ethereum",
  "checks": ["rugshield", "honeypot_check", "audit", "whale_scan"]
}
```

### Custom Watchlists

Use `risk_monitor` to set alerts for specific wallets or tokens:

```
POST /api/v1/x402-tools/risk_monitor
{
  "address": "0x...",
  "chain": "ethereum",
  "alerts": ["liquidity_change", "ownership_change", "whale_movement"]
}
```

### MCP Client Configuration

For production agents, set environment variables:

```json
{
  "mcpServers": {
    "rug-munch-intelligence": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://rugmunch.io/mcp"],
      "env": {
        "X402_WALLET": "0x...",
        "X402_FACILITATOR": "coinbase_cdp"
      }
    }
  }
}
```

---

## 10. Refunds & Support

### Refund Policy

- **Full refund** if a tool returns no data
- Request within **48 hours** of the call
- Post to `/api/v1/x402/refund` with your transaction hash
- Refunds are processed on-chain within 24 hours

### Getting Help

- **Documentation**: https://rugmunch.io/docs/mcp
- **FAQ**: https://rugmunch.io/docs/mcp#faq
- **Email**: mcp@rugmunch.io
- **GitHub**: https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────┐
│  RMI Quick Reference                            │
├─────────────────────────────────────────────────┤
│  MCP Endpoint:  https://rugmunch.io/mcp         │
│  Discovery:      https://rugmunch.io/.well-known │
│  API Base:       https://rugmunch.io/api/v1     │
│  Docs:           https://rugmunch.io/docs/mcp   │
├─────────────────────────────────────────────────┤
│  Free Trials:    1-5/tool (no wallet)            │
│  Paid Calls:    $0.01-$0.40 via x402            │
│  Chains:         13 (incl. BTC, TRON, SEPA)       │
│  Tools:          210 across 13 categories        │
├─────────────────────────────────────────────────┤
│  Refund: Full refund if no data, 48h window     │
│  Support: mcp@rugmunch.io                       │
└─────────────────────────────────────────────────┘
```

*© 2024–2026 Rug Munch Media LLC. All rights reserved.*
