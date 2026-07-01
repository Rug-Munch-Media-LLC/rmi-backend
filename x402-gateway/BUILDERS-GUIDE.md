# Rug Munch Intelligence — X402 MCP Server Builders Guide

## MUST READ for anyone building, maintaining, or debugging this system

**Last updated: May 23, 2026**
**Maintainer: Crypto Rug Muncher — biz@rugmunch.io**
**Entity: Rug Munch Media LLC — Wyoming, USA**

---

## TABLE OF CONTENTS
1. [Architecture Overview](#1-architecture-overview)
2. [Quick Start — 30 Seconds to Live](#2-quick-start--30-seconds-to-live)
3. [All Endpoints Reference](#3-all-endpoints-reference)
4. [Tool System — How Tools Work](#4-tool-system--how-tools-work)
5. [Payment Flow — How Money Works](#5-payment-flow--how-money-works)
6. [Deployment Guide](#6-deployment-guide)
7. [Directory Listings & Discovery](#7-directory-listings--discovery)
8. [Common Problems & Solutions](#8-common-problems--solutions)
9. [Adding New Tools](#9-adding-new-tools)
10. [Social & Community Links](#10-social--community-links)

---

## 1. ARCHITECTURE OVERVIEW

```
┌──────────────────────────────────────────────────────────────┐
│                     THE INTERNET                              │
│  AI Bots (Claude, Cursor, GPT)  │  Humans (Browsers, Apps)   │
└─────────────┬──────────────────────┬─────────────────────────┘
              │                      │
              ▼                      ▼
┌─────────────────────────┐  ┌─────────────────────────┐
│  sol.rugmunch.io         │  │  base.rugmunch.io        │
│  Cloudflare Worker       │  │  Cloudflare Worker       │
│  64 RMI + 154 MCP tools  │  │  64 RMI + 154 MCP tools  │
│  x402 payments           │  │  x402 payments           │
└───────────┬─────────────┘  └───────────┬─────────────┘
            │                             │
            ▼                             ▼
┌──────────────────────────────────────────────────────────────┐
│              mcp-router.rugmunch.io                           │
│              28 data providers — 154 tools                    │
│              DexScreener, Jupiter, Helius, etc.              │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│              rugmunch.io (Docker)                             │
│              /root/backend/ → rmi-backend:8000               │
│              FastAPI — 80+ modules                            │
│              x402 enforcement, human payments, tools API      │
└──────────────────────────────────────────────────────────────┘
```

### Key Facts
- **2 Cloudflare Workers** — sol.rugmunch.io (Solana) + base.rugmunch.io (Base)
- **71 RMI tools** per gateway — security, intelligence, market, social, analysis, launch, investigation
- **154 MCP tools** from 28 data providers
- **10 payment facilitators** — Coinbase CDP, PayAI, Cloudflare, Pieverse (BNB), AsterPay (EUR/SEPA), MERX (TRON), Primev (fee-free ETH), Satoshi (BTC), x402-rs (self-hosted), EIP-7702 (universal EVM)
- **13 payment chains** — Base, Solana, Ethereum, BSC, Polygon, Arbitrum, Optimism, TRON, Bitcoin, Avalanche, Fantom, Gnosis, SEPA/EUR
- **1 Docker backend** — FastAPI at /root/backend/, mounted to rmi-backend container
- **Smart router** — Auto-picks best facilitator per chain/token with automatic fallback
- **Cloudflare Tunnel** — rugmunch.io → rmi-backend:8000
- **Source code** — github.com/Rug-Munch-Media-LLC/rugmuncher-backend

### WORKER CODE LOCATION
```
/root/backend/x402-gateway/solana/    → Solana gateway
/root/backend/x402-gateway/base/      → Base gateway
```
Each has:
- `index.ts` — THE ENTIRE WORKER (single file, ~3000 lines)
- `wrangler.toml` — Environment bindings
- `smithery.json` — Smithery directory listing
- `glama.json` — Glama directory listing
- `dist/worker.js` — Compiled JavaScript (uploaded to Cloudflare)

### BACKEND CODE LOCATION
```
/root/backend/                        → Docker context
/root/backend/main.py                 → FastAPI entry point
/root/backend/app/routers/x402_tools.py      → x402 tools + human payment
/root/backend/app/routers/x402_enforcement.py → Payment middleware + smart router
/root/backend/app/facilitators/       → 10 facilitator modules (pluggable registry)
/root/backend/app/facilitators/router.py     → Smart router (auto-picks best facilitator)
/root/backend/docker/x402-rs/         → Self-hosted Rust facilitator Docker config
```

---

## 2. QUICK START — 30 SECONDS TO LIVE

### For AI Bots (MCP Protocol)
```bash
# One command — no API key, no signup, no limits on trials
npx -y mcp-remote@latest https://sol.rugmunch.io/mcp
```

### For Claude Desktop
Add to `claude_desktop_config.json`:
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

### For Humans (Browser)
1. Go to https://rugmunch.io
2. Connect wallet (MetaMask, WalletConnect, Coinbase, Phantom)
3. Pick a tool, pay with USDC/SOL/ETH/USDT
4. Get results instantly

### For Developers (curl)
```bash
# List all 218 tools
curl -X POST https://sol.rugmunch.io/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Call a tool
curl -X POST https://sol.rugmunch.io/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"audit","arguments":{"address":"TOKEN_ADDRESS","chain":"solana"}}}'
```

---

## 3. ALL ENDPOINTS REFERENCE

### Solana Gateway (sol.rugmunch.io)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Worker health + tool counts |
| `/mcp` | POST | MCP JSON-RPC protocol (tools/list, tools/call) |
| `/mcp-x402-docs` | GET | Full JSON bot documentation |
| `/llms.txt` | GET | AI agent discovery (plain text) |
| `/.well-known/x402` | GET | x402 payment discovery |
| `/frameworks` | GET | All framework formats (OpenAI, Anthropic, etc.) |
| `/openai-tools` | GET | OpenAI function-calling format |
| `/anthropic-tools` | GET | Anthropic tool format |
| `/gemini-tools` | GET | Gemini function declarations |
| `/langchain-tools` | GET | LangChain tool format |
| `/tools/{name}` | POST/GET | REST API for individual tools |
| `/pricing` | GET | HTML pricing page |
| `/tip` | GET | Donation wallet addresses |
| `/about` | GET | Organization info |

### Base Gateway (base.rugmunch.io)
Identical endpoints, different network (Base Mainnet / eip155:8453).

### Backend (rugmunch.io)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Backend health |
| `/api/v1/x402/dashboard` | GET | Earnings + trial stats |
| `/api/v1/x402/tools-catalog` | GET | Tool catalog with pricing |
| `/api/v1/x402-tools/human-execute` | POST | Human wallet payment execution |
| `/api/v1/x402-tools/mcp-proxy` | POST | External MCP tool proxy |

---

## 4. TOOL SYSTEM — HOW TOOLS WORK

### RMI Tools (64 per gateway)
Defined in `RMI_TOOLS` object in `index.ts`. Each tool has:
```typescript
{
  name: "audit",
  description: "Deep smart contract audit...",
  price: "$0.05",
  priceAtomic: "50000",       // USDC amount in smallest unit
  category: "security",
  trialFree: 1,                // Number of free calls
  method: "POST"               // HTTP method for REST endpoint
}
```

### Tool Execution Flow
```
MCP tools/call → executeToolDirect() → specific execute function
REST /tools/{name} → executeToolDirect() → specific execute function
```

`executeToolDirect()` is a big switch statement in both worker files (~line 120).
Each tool name maps to a specific async function that:
1. Fetches data from external APIs (DexScreener, CoinGecko, etc.)
2. Processes with fallback chains (if API 1 fails, try API 2)
3. Returns structured result

### Adding a New Tool
1. Add entry to `RMI_TOOLS` object
2. Add `else if` case in `executeToolDirect()`
3. Create the execution function
4. Recompile: `npx esbuild index.ts --bundle --format=esm --platform=neutral --target=es2022 --outfile=dist/worker.js --external:node:*`
5. Upload to Cloudflare via REST API (see Deployment section)

### MCP External Tools (154 across 28 services)
Fetched from `mcp-router.rugmunch.io/tools` at runtime. Cached for 5 minutes.
Each service provides tool metadata with parameters and descriptions.
Services: dexscreener, jupiter, pumpfun, raydium, defillama, dexpaprika, coincap, coinmarketcap, cryptopanic, cryptocompare, blockchair, blockchain, mempool, solana, helius, birdeye, coingecko, cryptoiz, blockrun, agentfi, moralis, gmgn, nansen, arkham, dune, solscan, quicknode, freeusdc

---

## 5. PAYMENT FLOW — HOW MONEY WORKS

### Bot Payment (x402 Protocol — Multi-Chain)
1. Client calls tool → receives 402 Payment Required
2. 402 response lists ALL 13 chains + 10 facilitators + accepted tokens
3. Client pays on ANY supported chain (USDC, USDT, BTC, EUR, etc.)
4. Client includes `X-Pay` header with payment proof (tx hash + signature)
5. **Smart router** detects chain/token from payload
6. Router picks best facilitator for that chain (auto-fallback if primary fails)
7. Payment verified → tool executes → result returned
8. Trial mode: first 1-5 calls per tool are FREE

### Human Payment (Wallet Connect)
1. User connects wallet (MetaMask/WalletConnect/Coinbase/Phantom)
2. Selects tool + payment token (USDC-SOL, USDC-Base, SOL, ETH, USDT, BTC, TRX, EUR)
3. Signs transaction in wallet
4. Frontend sends tx_hash to `/api/v1/x402-tools/human-execute`
5. Backend verifies tx via appropriate facilitator (auto-routed)
6. If confirmed → executes tool via gateway → returns result
7. Payment logged in Supabase `x402_payments` table

### Payment Addresses (ALL YOUR WALLETS)
| Chain | Address | Assets |
|-------|---------|--------|
| Base/EVM | `0x1E3AC01d0fdb976179790BDD02823196A92705C9` | USDC, ETH, USDT |
| Solana | `Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv` | USDC, SOL |
| TRON | (set X402_TRON_PAY_TO) | USDT, USDC, USDD |
| Bitcoin | (set X402_BTC_PAY_TO) | BTC |
| SEPA/EUR | (set ASTERPAY_SEPA_IBAN) | EUR |

### Refund Policy
Auto-refund if tool returns no data. Request within 48h via POST /api/v1/x402/refund.

---

## 6. DEPLOYMENT GUIDE

### Deploying Gateway Workers

**Method 1: REST API (use when wrangler auth fails)**
```bash
CF_TOKEN="cfat_YOUR_TOKEN"
ACCT="8f9bd9165c1250b426c66dc1967deefd"

# 1. Compile TypeScript
cd /root/backend/x402-gateway/solana
npx esbuild index.ts --bundle --format=esm --platform=neutral \
  --target=es2022 --outfile=dist/worker.js --external:node:*

# 2. Upload to Cloudflare
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCT/workers/scripts/x402-sol" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -F 'metadata={"main_module":"worker.js","bindings":[...]};type=application/json' \
  -F "worker.js=@dist/worker.js;type=application/javascript+module"
```

**Method 2: Wrangler (when auth works)**
```bash
cd /root/backend/x402-gateway/solana
CLOUDFLARE_API_TOKEN=$CF_TOKEN npx wrangler deploy
```

**WARNING: Wrangler requires Account Membership permissions.**
If you get error 9106 "Authentication failed on /memberships", use Method 1.
API tokens with Workers:Edit permission work for the REST API but not wrangler.

### Required Worker Bindings
```
BACKEND_API     = https://rugmunch.io
BACKEND_MCP     = https://mcp-router.rugmunch.io
NETWORK         = solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp (or eip155:8453 for Base)
NETWORK_NAME    = Solana Mainnet (or Base Mainnet)
FACILITATOR     = https://facilitator.payai.network (or https://facilitator.x402.org)
ORGANIZATION    = Rug Munch Intelligence
PAY_TO          = Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv (or 0x1E... for Base)
GW_URL          = https://sol.rugmunch.io (or base.rugmunch.io)
SOLANA_GATEWAY  = https://sol.rugmunch.io
```

### Restarting Backend
```bash
docker restart rmi-backend
# Wait ~10 seconds for FastAPI to load all modules
curl http://localhost:8000/health  # Verify
```

### Cloudflare Email Routing
Email for both rugmunch.io and cryptorugmunch.com routes to cryptorugmuncher@gmail.com.
Managed via Cloudflare Dashboard → Email → Email Routing.
- biz@rugmunch.io → cryptorugmuncher@gmail.com
- info@rugmunch.io → cryptorugmuncher@gmail.com
- *@cryptorugmunch.com → cryptorugmuncher@gmail.com (catch-all)

### Custom Domain Routes
```
sol.rugmunch.io/*   → x402-sol worker
base.rugmunch.io/*  → x402-base worker
```

These are set via Cloudflare Workers Routes in the dashboard.
DNS CNAME records point to the workers.dev subdomains with proxy enabled.

---

## 7. DIRECTORY LISTINGS & DISCOVERY

### Active Listings
| Directory | Status | URL |
|-----------|--------|-----|
| **Smithery** | Active | smithery.ai/server/@cryptorugmunch/x402 |
| **Glama** | Active | glama.ai/mcp/servers/rugmunch |
| **mcp.so** | Submitted | Sol UUID: 2102ac31 | Base UUID: 53a79a21 |
| **GitHub** | Public | github.com/Rug-Munch-Media-LLC |
| **Coinbase CDP** | Register at | portal.cdp.coinbase.com (set CDP_API_KEY_ID + CDP_API_KEY_SECRET) |

### Discovery Files
| File | Location | Purpose |
|------|----------|---------|
| `smithery.json` | Gateway root | Smithery listing |
| `glama.json` | Gateway root | Glama listing |
| `X402-SYSTEM.md` | /root/backend/x402-gateway/ | Multi-facilitator system documentation |
| `BUILDERS-GUIDE.md` | /root/backend/x402-gateway/ | This guide |
| README.md | Gateway root | Quick start |

### Bot Discovery Endpoints
Bots discover this service through:
1. `/mcp-x402-docs` — Full JSON docs with stats, endpoints, pricing, socials
2. `/llms.txt` — AI agent discovery standard
3. `/.well-known/x402` — x402 payment protocol discovery
4. MCP `tools/list` — Standard MCP tool catalog
5. Directory listings (Smithery, Glama, mcp.so)

---

## 8. COMMON PROBLEMS & SOLUTIONS

### "MCP tools show 0 on custom domains but 154 on workers.dev"
**Cause:** Cold start — MCP_TOOLS module variable not yet populated.
**Fix:** Hit the health endpoint once. MCP tools cache for 5 minutes.
The first request to a cold worker instance fetches from mcp-router.

### "tools/call returns 'Method not found'"
**Cause:** Old worker version before tools/call was implemented.
**Fix:** Redeploy with latest code (May 23, 2026 or newer).

### "Wrangler deploy fails with 9106 auth error"
**Cause:** API token lacks Account Membership permissions.
**Fix:** Use REST API deployment (Method 1 in Deployment section).
Get a new token at dash.cloudflare.com → API Tokens with Workers:Edit.

### "Backend returns 500 on human-execute"
**Cause:** x402 enforcement middleware blocking the endpoint.
**Fix:** Ensure `/api/v1/x402-tools/human-execute` is in FREE_PATHS
in x402_enforcement.py and the bot-agent check exempts it.

### "Human-execute returns empty response"
**Cause:** Backend container just restarted, FastAPI still loading.
**Fix:** Wait 10-15 seconds. Check `/health` endpoint first.

### "Token expired / Invalid API Token"
**Cause:** Cloudflare API tokens can be revoked or expire.
**Fix:** Generate new token at dash.cloudflare.com → API Tokens.
Current working token stored in `/root/.hermes/.env` as CLOUDFLARE_API_TOKEN.

---

## 9. ADDING NEW TOOLS

### Step-by-Step
1. **Define the tool** in RMI_TOOLS (~line 25 in index.ts):
```typescript
my_new_tool: {
  name: "my_new_tool",
  description: "What this tool does",
  price: "$0.05",
  priceAtomic: "50000",
  category: "security",  // security, intelligence, market, analysis, social, launchpad
  trialFree: 2,
  method: "POST"
}
```

2. **Add dispatch case** in executeToolDirect (~line 120):
```typescript
} else if (toolName === "my_new_tool") {
  return await executeMyNewTool(body?.address || "", body?.chain || "solana");
}
```

3. **Create execution function** after the dispatch:
```typescript
async function executeMyNewTool(address: string, chain: string): Promise<any> {
  const result: any = { tool: "my_new_tool", address, chain, timestamp: new Date().toISOString(), sources_used: [] };
  try {
    const resp = await fetch("https://api.example.com/data/" + address);
    if (resp.ok) {
      const data = await resp.json();
      result.data = data;
      result.sources_used.push("example_api");
    }
  } catch (e) { console.debug("My tool failed:", e); }
  return result;
}
```

4. **Compile + Upload + Verify**:
```bash
npx esbuild index.ts --bundle --format=esm --platform=neutral --target=es2022 --outfile=dist/worker.js --external:node:*
# Upload via REST API (see Deployment section)
# Test:
curl -X POST https://sol.rugmunch.io/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"my_new_tool","arguments":{"address":"test"}}}'
```

---

## 10. SOCIAL & COMMUNITY LINKS

| Platform | Link | Purpose |
|----------|------|---------|
| **X/Twitter** | [@cryptorugmunch](https://x.com/cryptorugmunch) | Main account |
| **Telegram** | [t.me/cryptorugmunch](https://t.me/cryptorugmunch) | Main channel |
| **Telegram Alerts** | [t.me/cryptorugmuncher](https://t.me/cryptorugmuncher) | Scam alerts + updates |
| **GitHub Org** | [Rug-Munch-Media-LLC](https://github.com/Rug-Munch-Media-LLC) | All repos |
| **GitHub Backend** | [rugmuncher-backend](https://github.com/Rug-Munch-Media-LLC/x402-gateway-solana) | Main repo |
| **Website** | [rugmunch.io](https://rugmunch.io) | Web app |
| **Email** | biz@rugmunch.io | Business inquiries |

### Token & DAO
- V2 token launch planned mid-2026
- 1:1 airdrop checker for v1 $CRM and $cryptorugmunch holders
- Verify eligibility and claim tokens on launch day
- Full DAO LLC transition under Wyoming state law within 12 months
- Community governance via token-based voting

### Trust & Transparency
- Rug Munch Media LLC — Wyoming, USA
- 20-year teacher turned full-time crypto scam investigator
- Bootstrapped. No VC. Revenue from tool calls only
- Backend is open source
- No API keys stored. Payments verified on-chain
- Auto-refund if tool fails

---

## QUICK REFERENCE CARD

```
DEPLOY:        esbuild → REST API PUT → verify health
RESTART:       docker restart rmi-backend → wait 10s → curl health
ADD TOOL:      RMI_TOOLS entry → dispatch case → function → compile → upload
TEST:          curl POST /mcp tools/list → tools/call
DEBUG:         docker logs rmi-backend --tail 50
TOKEN:         /root/.hermes/.env CLOUDFLARE_API_TOKEN
ACCOUNT ID:    8f9bd9165c1250b426c66dc1967deefd
ZONE rugmunch: 2c5c82d253dbfb7e091eb8b1dac4f0f8
ZONE cryptorug: 25168c58ff5783c51f047378eac525cb
```

---

**This document is the single source of truth for the RMI x402 system.**
**If you find something wrong, outdated, or missing — fix it here.**
**Questions? biz@rugmunch.io | @cryptorugmunch on X**
