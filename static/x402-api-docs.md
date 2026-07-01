# RMI x402 API — Crypto Intelligence Micropayments

> **210 tools across 13 chains. Pay per call with USDC on any chain.**

## Quick Start

### 1. Discover Available Tools

```bash
curl https://rugmunch.io/.well-known/x402
```

Returns the full x402 discovery document with all tools, prices, supported chains, and facilitators.

### 2. Browse the Catalog

```bash
curl https://rugmunch.io/api/v1/x402/tools-catalog
```

### 3. Make a Paid Request

```bash
# Example: Check if a token is a honeypot (Base, $0.05 USDC)
curl -X POST https://rugmunch.io/api/v1/x402-tools/honeypot_check \
  -H "Content-Type: application/json" \
  -H "X-Pay: <your-signed-payment>" \
  -d '{"chain": "base", "address": "0x..."}'
```

See the [x402 protocol spec](https://x402.org) for payment header details.

## Supported Chains

| Chain | USDC Contract | Facilitators |
|-------|-------------|--------------|
| **Base** | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` | Coinbase CDP, PayAI, EIP-7702 |
| **Ethereum** | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | Primev, PayAI, EIP-7702 |
| **Solana** | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` | PayAI |
| **Arbitrum** | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` | EIP-7702 |
| **Optimism** | `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85` | EIP-7702 |
| **Polygon** | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` | EIP-7702 |
| **BSC** | `0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d` | EIP-7702 |
| **Avalanche** | `0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E` | EIP-7702 |
| **Fantom** | `0x04068DA6C83AFCFA0e13ba15A6696662335D5B75` | EIP-7702 |
| **Gnosis** | `0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83` | EIP-7702 |
| **TRON** | USDT/USDC/USDD | Self-verified (fee-free) |
| **Bitcoin** | BTC | Self-verified (fee-free, 1-conf) |
| **SEPA/EUR** | Fiat EUR off-ramp | AsterPay |

## Tool Categories

| Category | Tools | Price Range |
|----------|-------|-------------|
| **Security** | Honeypot Check, Rug Pull Predictor, Audit, MEV Protection | $0.01–$0.50 |
| **Intelligence** | OSINT Identity Hunt, Insider Analysis, Whale Tracking | $0.01–$0.25 |
| **Trading** | Smart Money Alpha, Sniper Alert, Sentiment | $0.01–$0.10 |
| **Forensics** | Deep Scan, Deployer History, Wallet Dossier | $0.05–$0.25 |
| **DeFi** | Yield Scanner, Liquidity Depth, Impermanent Loss | $0.01–$0.10 |
| **NFT** | Wash Trading Detection, Floor Analytics | $0.10 |
| **Social** | Twitter Profile/Timeline/Search, Discord Alpha | $0.01 |

## Free Trials

Every tool offers free trials without a wallet:
- **No wallet**: 1 free call per tool
- **Connected wallet**: 3 free calls per standard tool, 1 per premium tool

## Facilitators

| Facilitator | Settlement | Fee | Chains |
|-------------|-----------|-----|--------|
| **Coinbase CDP** | Instant | Free | Base, Polygon, Arbitrum, Solana |
| **Primev (mev-commit)** | Pre-confirmed | Free | Ethereum |
| **EIP-7702** | Instant | Free | BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base |
| **PayAI** | Deferred | Small fee | Base, Solana |
| **TRON Self-Verify** | Instant | Free | TRON |
| **Bitcoin Self-Verify** | 1-conf | Free | Bitcoin |
| **AsterPay** | SEPA off-ramp | Fee | EUR fiats |

## Payment Wallets

- **EVM**: `0x1E3AC01d0fdb976179790BDD02823196A92705C9`
- **Solana**: `Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv`
- **TRON**: `TY27YaMiiXdJ6JcjnLdfvoACxT16V6vu5q`
- **Bitcoin**: `17PiUt1iqfAfM7o7abHaR3GUZaNHBpn7Fz`

## Refund Policy

Full refund if a tool returns no data. Request within 48h via `POST /api/v1/x402/refund` with your transaction hash.

## Protocol Compliance

- [x] x402 v2 discovery at `/.well-known/x402`
- [x] HTTP 402 status for unpaid requests
- [x] Multiple facilitator support
- [x] Free trial tier
- [x] Refund endpoint

## Links

- **Discovery**: https://rugmunch.io/.well-known/x402
- **API Docs**: https://rugmunch.io/docs
- **Health**: https://rugmunch.io/health
- **GitHub**: https://github.com/Rug-Munch-Media-LLC
