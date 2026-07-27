# Rug Munch Intelligence x402 — Multi-Facilitator Payment System

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENTS                              │
│  MCP Clients (Claude, Cursor)  │  Bots  │  Humans (Web UI)  │
└────────────┬──────────────────────┬──────────────┬──────────┘
             │                      │              │
             ▼                      ▼              ▼
┌────────────────────────┐  ┌──────────────┐  ┌──────────────┐
│  sol.rugmunch.io       │  │base.rugmunch │  │ rugmunch.io  │
│  (Solana Gateway)      │  │  .io (Base)  │  │  (Web App)   │
│  Cloudflare Worker     │  │  CF Worker   │  │  Vercel/CF   │
└───────────┬────────────┘  └──────┬───────┘  └──────┬───────┘
            │                      │                  │
            ▼                      ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│           SMART FACILITATOR ROUTER (auto-picks best)         │
│                                                             │
│  Coinbase CDP  PayAI   Cloudflare  Pieverse   AsterPay      │
│  (Base/Poly/    (Base/  (Eth/Base   (BNB      (EUR/SEPA    │
│   Arb/Sol)      Sol)    Sepolia)    Chain)    off-ramp)     │
│                                                             │
│  MERX TRON     Primev     Satoshi    x402-rs    EIP-7702    │
│  (TRON USDT/   (Eth       (BTC→     (self-     (all EVM    │
│   USDC/USDD)   fee-free)  Base/Sol)  hosted)    chains)     │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   BACKEND (rugmunch.io)                      │
│  FastAPI — 90+ modules  |  13 payment chains                 │
│  /api/v1/x402-tools/* — 64+ RMI security tools               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              DATA PROVIDERS (28 services)                    │
│  DexScreener, Jupiter, Helius, Birdeye, CoinGecko, Nansen,   │
│  Arkham, GMGN, Moralis, PumpFun, Raydium, DeFiLlama + 16    │
└─────────────────────────────────────────────────────────────┘
```

## Payment Chains & Facilitators

| Chain | Facilitators | Tokens | Settlement |
|-------|-------------|--------|------------|
| Base | Coinbase CDP (free), PayAI, EIP-7702 | USDC | Instant/Deferred |
| Solana | Coinbase CDP (free), PayAI | USDC | Instant/Deferred |
| Ethereum | Primev (free), PayAI, Cloudflare, EIP-7702 | USDC/USDT/DAI/ETH | Fee-free |
| BNB Chain | Pieverse, EIP-7702 | USDC/USDT | Instant |
| Polygon | Coinbase CDP, EIP-7702 | USDC | Instant |
| Arbitrum | Coinbase CDP, EIP-7702 | USDC | Instant |
| TRON | MERX x402 | USDT/USDC/USDD | Sub-3s |
| Bitcoin | Satoshi Facilitator | BTC→Base/Sol | Cross-chain |
| Avalanche | EIP-7702 | USDC/AVAX | Self-verify |
| Fantom | EIP-7702 | USDC/FTM | Self-verify |
| Gnosis | EIP-7702 | USDC/XDAI | Self-verify |
| Optimism | EIP-7702 | USDC/ETH | Self-verify |
| SEPA/EUR | AsterPay (MiCA) | EUR/USDC | Fiat off-ramp |

## Quick Start — MCP Client

### Smithery
```json
{
  "mcpServers": {
    "rmi-solana": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://sol.rugmunch.io/mcp"]
    }
  }
}
```

### Glama
```json
{
  "mcpServers": {
    "rmi-base": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://base.rugmunch.io/mcp"]
    }
  }
}
```

### Claude Desktop
Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "rugmunch": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://sol.rugmunch.io/mcp"]
    }
  }
}
```

## API Endpoints

### Discovery
| Endpoint | Description |
|----------|-------------|
| `/.well-known/x402` | x402 multi-facilitator payment discovery (13 chains, 10 facilitators) |
| `/llms.txt` | AI agent discovery (llms.txt standard) |
| `/openai-tools` | OpenAI function calling format |
| `/anthropic-tools` | Anthropic tool format |
| `/gemini-tools` | Gemini function declarations |
| `/langchain-tools` | LangChain tool format |
| `/mcp-tools` | MCP tool catalog |

### MCP Protocol
| Method | Description |
|--------|-------------|
| `tools/list` | List all 218 tools |
| `tools/call` | Execute any tool |
| `initialize` | Protocol handshake |
| `ping` | Health check |

### REST API
| Endpoint | Description |
|----------|-------------|
| `/api/v1/x402/stats` | Live facilitator stats, chain coverage, router metrics |
| `/api/v1/x402/transparency` | Public payment ledger |
| `/api/v1/x402/ledger` | Anonymized payment history |
| `/api/v1/x402/receipt/{id}/verify` | Public receipt verification |
| `/api/v1/x402/refund` | Refund request endpoint |
| `/api/v1/x402-tools/discovery` | Full tool catalog + chain options |
| `/tools/{name}` | Execute tool via REST |
| `/health` | Worker health + tool counts |
| `/about` | Organization + tool listing |

## Payment Flow

### x402 Bot Payment (Multi-Chain)
1. Bot requests tool → receives 402 Payment Required
2. 402 response lists ALL 13 chains + facilitators + tokens
3. Bot pays on ANY supported chain (USDC, USDT, BTC, EUR, etc.)
4. Bot includes `X-Pay` header with payment proof
5. Smart router auto-picks best facilitator for that chain/token
6. Payment verified → tool executes → result returned
7. If facilitator fails, router auto-falls to next best

### Router Logic
- Chain match → Bot pays on TRON → MERX TRON handles it
- Token match → Bot pays USDT → Pieverse on BSC, MERX on TRON
- Health → Unhealthy facilitators skipped
- Priority → Fee-free > instant > deferred
- Auto-fallback → CDP quota exceeded → PayAI takes over

### Human Payment (Web)
1. User connects wallet (Phantom, MetaMask, WalletConnect, Coinbase Wallet)
2. Selects tool and payment method (USDC-SOL, USDC-Base, SOL, ETH, USDT)
3. Signs transaction in wallet
4. Payment verified on-chain via appropriate facilitator
5. Tool executes, result displayed

### Trial Mode
Each tool has 1-5 free trial calls. No payment needed for initial testing.

## Tool Categories

### Security (22 tools)
audit, rugshield, honeypot_check, mev_protection, bridge_security, rug_pull_predictor, urlcheck, risk_monitor, nft_wash_detector, anomaly, profile_flip, fresh_pair, clone_detect, deployer_history, token_age, scam_database, mev_alert, wash_trading, bundler_detect, liquidity_migration, sniper_alert, gas_forecast

### Intelligence (18 tools)
whale, whale_scan, whale_profile, smartmoney, cluster, insider, sniper_detect, syndicate_scan, syndicate_track, wallet_graph, launch_intel, copy_trade_finder, liquidity_flow, defi_yield_scanner, alpha_digest, insider_network, listing_predictor, kol_performance

### Market (8 tools)
pulse, market_overview, chain_health, token_deep_dive, liquidity_depth, unlock_calendar, arbitrage_scan, whale_accumulation

### Analysis (6 tools)
wallet, forensics, portfolio_tracker, token_comparison, portfolio_aggregate, wallet_pnl

### Social (6 tools)
sentiment, social_signal, tw_profile, tw_timeline, tw_search, sentiment_spike

### Launch (4 tools)
launch, sniper_alert, airdrop_finder, airdrop_check

### Premium Investigation (4 tools)
forensic_valuation, osint_identity_hunt, investigation_report, forensic_pack

### Data Providers (154 tools across 28 services)
DexScreener, Jupiter, PumpFun, Raydium, DeFiLlama, DexPaprika, CoinCap, CoinMarketCap, CryptoPanic, CryptoCompare, Blockchair, Blockchain.com, Mempool, Solana RPC, Helius, Birdeye, CoinGecko, CryptoIZ, Blockrun, AgentFi, Moralis, GMGN, Nansen, Arkham, Dune, Solscan, QuickNode, FreeUSDC

## Links

- **Solana Gateway**: https://sol.rugmunch.io
- **Base Gateway**: https://base.rugmunch.io
- **MCP Catalog**: https://sol.rugmunch.io/mcp
- **Smithery**: https://smithery.ai/server/@cryptorugmunch/x402
- **Glama**: https://glama.ai/mcp/servers/rugmunch
- **GitHub**: https://github.com/Rug-Munch-Media-LLC/rugmuncher-backend
- **Website**: https://rugmunch.io
- **X/Twitter**: https://x.com/cryptorugmunch

## Pricing
All tools priced $0.01 - $0.40 USDC per call. Trial mode: 1-5 free calls per tool.
Multi-chain: pay on any supported chain with any supported token.
Fee-free options: Coinbase CDP (1K tx/mo free) and Primev FastRPC (always free on Ethereum).

## Facilitator Registry
Source: `/path/to/backend/app/facilitators/` — 14 modules, pluggable architecture.
Startup: `register_all_facilitators()` called from main.py — auto-detects and registers.
