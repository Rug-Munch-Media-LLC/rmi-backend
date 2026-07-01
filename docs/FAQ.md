# RMI Platform — Frequently Asked Questions

## General

**What is Rug Munch Intelligence (RMI)?**
A unified crypto intelligence platform with 270+ tools for token security, wallet forensics, whale tracking, market data, and blockchain queries. Free tools available, paid tools via x402 micropayments.

**How many tools are free?**
Every paid tool includes 1-5 free trial calls. Connecting a wallet unlocks full trial allotment (3-5 for basic tools, 1-2 for premium). Bots without wallets get 1 trial per tool via fingerprint.

**What chains are supported?**
Solana, Ethereum, Base, BSC, Polygon, Arbitrum, Optimism, Avalanche, Fantom, Gnosis, TRON, Bitcoin (12 payment chains, 38 data chains).

## Pricing & Payments

**How does x402 pricing work?**
You pay per tool call in USDC. Prices range from $0.01 (basic lookups) to $0.25 (institutional forensics). Payment happens automatically via the x402 protocol — no accounts, no subscriptions, no prepayment.

**Which chains can I pay on?**
Base, Solana, Ethereum, BSC, TRON, Bitcoin, Polygon, Arbitrum, Optimism, Avalanche, Fantom, Gnosis. Plus EUR/SEPA via AsterPay for European users.

**What if a paid tool returns no data?**
Full automatic refund within 48 hours. You're never charged for empty results.

**Do free trials reset?**
Yes — every 24 hours. Fingerprint gating ensures fair usage:
- **Humans (wallet connected)**: Full trial allotment (3-5 for basic, 1-2 for premium)
- **Bots (device fingerprint)**: 1 trial per tool, then payment required
- **IP-only**: 1 trial max, then payment required

## DataBus

**What is DataBus?**
The single data pipeline powering all 270+ tools. 38 data chains, 67 providers, automatic failover. You never need to specify which provider to use — just request the data type and DataBus handles the rest.

**What happens when a source fails?**
DataBus automatically tries the next provider in the chain. Token prices try: local cache → Jupiter → DexScreener → Binance → CoinGecko. You always get data.

## Endpoints

| Path | Purpose |
|------|---------|
| `/api/v1/x402-databus/*` | DataBus direct endpoints (40 tools) |
| `/api/v1/x402-tools/*` | Legacy tool endpoints (230+ tools, DataBus fallback) |
| `/api/v1/catalog` | Human-facing catalog (search, featured, by-trial) |
| `/mcp/tools` | MCP-compliant tool catalog |
| `/.well-known/x402` | x402 discovery document |

## Security

- API keys encrypted in GPG vault, never in plaintext
- No source provider names exposed in responses
- 6-tier access control: public, authenticated, basic, premium, admin, x402_paid
- VPN-resistant fingerprinting for trial gating
