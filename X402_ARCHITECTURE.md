# x402 Protocol — Complete System Architecture
## MUST READ for all future RMI developers
### Auto-audited: May 23, 2026 — 59 tools, 7 chains, all endpoints verified

---

## SYSTEM OVERVIEW

```
                     INTERNET
                        │
                        ▼
              Cloudflare Tunnel (rmi-cloudflare)
              ┌─────────────────────────────┐
              │  rugmunch.io                │
              │  mcp.rugmunch.io            │
              │  n8n.rugmunch.io            │
              └─────────────┬───────────────┘
                            │
              ┌─────────────▼───────────────┐
              │  nginx (:80, :443)          │
              │  Routes:                    │
              │    /api/*     → :8000       │
              │    /.well-known/* → :8000   │
              │    /mcp/*     → :8000       │
              │    /health    → :8000       │
              │    /          → static      │
              └─────────────┬───────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
   ┌─────────┐      ┌──────────┐        ┌──────────┐
   │ Backend │      │ Orchestrator│      │   n8n    │
   │  :8000  │      │   :8081    │      │  :5678   │
   │ 59 tools│      │ 9 agents   │      │ 2 flows  │
   └────┬────┘      └──────────┘        └──────────┘
        │
   ┌────┼────────────────────┐
   │    │                    │
   ▼    ▼                    ▼
┌──────┐ ┌──────┐     ┌──────────┐
│Redis │ │Supabase│    │ Langfuse │
│:6379 │ │ (API) │     │  :3100   │
└──────┘ └──────┘     └──────────┘
```

---

## FILE MAP — Every x402 file and what it does

### Core Backend (Python/FastAPI)

| File | Lines | Purpose |
|------|-------|---------|
| `app/routers/x402_enforcement.py` | 1290 | **Payment gatekeeper** — intercepts all `/api/v1/x402-tools/*`, verifies x402 payment headers, enforces trials, builds 402 Payment Required responses. 7-chain support. |
| `app/routers/x402_tools.py` | 3581 | **Tool handlers** — 48 route implementations. Each `@router.post("/audit")` is a tool. Also serves AI framework adapters (OpenAI, Anthropic, Gemini, LangChain formats). |
| `app/routers/x402_catalog.py` | 255 | **Auto-discovery** — parses gateway index.ts files + scans route decorators. Builds unified catalog. No hardcoded tool lists. |
| `app/routers/x402_forensic_tools.py` | 237 | **Forensic bundles** — 3 premium tools: forensic_valuation, osint_identity_hunt, investigation_report |
| `app/routers/x402_dashboard.py` | 493 | **Analytics** — usage tracking, revenue per tool, top users, trial exhaustion stats |
| `app/routers/x402_middleware.py` | 685 | **Anti-abuse** — device fingerprinting, trial tracking per device/wallet, rate limiting |
| `app/mcp/x402_mcp_server.py` | 682 | **MCP protocol server** — translates x402 tools into MCP format for Claude/Cursor/Windsurf |

### Cloudflare Workers (TypeScript)

| File | Lines | Purpose |
|------|-------|---------|
| `x402-gateway/base/index.ts` | 2650 | **Base + EVM gateway** — Payment verification via PayAI facilitator. 44 tool definitions. Routes to backend. |
| `x402-gateway/solana/index.ts` | 2650 | **Solana gateway** — Payment verification via PayAI facilitator. 35 tool definitions. Routes to backend. |
| `x402-twitter-view/src/index.ts` | ~200 | **Twitter data worker** — profiles, timelines, search. Self-healing with failover. |

### GitHub Repos (public)

| Repo | Purpose |
|------|---------|
| `rug-munch-intelligence-mcp` | Public pip package. Thin MCP wrapper around x402 API. |
| `x402-gateway-solana` | Solana gateway source — deploys to Cloudflare Workers |
| `x402-gateway-base` | Base + EVM gateway source — deploys to Cloudflare Workers |
| `x402-twitter-view` | Twitter data worker source |

---

## PAYMENT FLOW — Step by step

```
1. User/bot calls POST /api/v1/x402-tools/{tool}
       │
2.     x402_enforcement middleware intercepts
       ├── Check: Has user paid? (x-pay header with tx hash)
       ├── Check: Is trial available? (device fingerprint + wallet)
       ├── If unpaid AND no trials → build 402 Payment Required
       │     └── Returns: payment addresses per chain, amounts, timeout
       │
3.     If paid or trial available → forward to tool handler
       │
4.     Tool handler (x402_tools.py) executes
       ├── Call backend connectors (Helius, Etherscan, DeFiLlama, etc.)
       ├── Aggregate multi-source data
       └── Return JSON response
       │
5.     Payment verification (if paid):
       ├── Base/Solana → PayAI facilitator verifies USDC transfer
       └── ETH/BSC/ARB/OPT/POL → Self-verify via Etherscan on-chain check
```

### Payment Addresses
- **All EVM chains**: `0x1E3AC01d0fdb976179790BDD02823196A92705C9`
- **Solana**: `Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv`
- **Token**: USDC on all chains
- **Amounts**: $0.01 - $0.50 per tool (defined in gateway index.ts)

---

## TOOL DISCOVERY — How the catalog works

```
MCP Catalog (/api/v1/x402/tools-catalog)
    │
    ├── Step 1: parse_gateway_tools()
    │   └── Reads x402-gateway/{base,solana}/index.ts
    │   └── Regex extracts each tool from RMI_TOOLS object
    │   └── Finds: name, description, price, category, trialFree, method
    │   └── Result: 44 tools (22 unique to base, 0 unique to solana)
    │
    ├── Step 2: discover_route_tools()
    │   └── Scans x402_tools.py + x402_forensic_tools.py
    │   └── Finds @router.get/post decorators
    │   └── Extracts docstrings as descriptions
    │   └── Result: 5 unique tools not in gateways
    │
    └── Step 3: Merge + Deduplicate
        └── Same tool ID = merge chains
        └── Result: 59 total tools
```

### Output formats
| Format | Endpoint | For |
|--------|----------|-----|
| Full catalog | `/api/v1/x402/tools-catalog` | Humans, dashboards |
| x402 protocol | `/.well-known/x402` | AI agents, protocol discovery |
| OpenAI | `/api/v1/x402-tools/openai-tools` | ChatGPT, OpenAI-compatible |
| Anthropic | `/api/v1/x402-tools/anthropic-tools` | Claude, Cursor |
| Gemini | `/api/v1/x402-tools/gemini-tools` | Google Gemini |
| LangChain | `/api/v1/x402-tools/langchain-tools` | LangChain agents |

---

## PRICING & TRIALS

| Tier | Calls | Requirement |
|------|-------|-------------|
| Anonymous | 1 free per tool | Device fingerprint |
| Wallet connected | 3 free per tool | MetaMask/Phantom |
| Paid | Unlimited | USDC payment per call |

**Refund**: Full refund if tool returns no data. POST `/api/v1/x402/refund` with tx hash within 48h.

**Anti-abuse**: Device fingerprinting survives VPN/incognito. Identity hierarchy: wallet > device_id > turnstile > fingerprint.

---

## CHAIN SUPPORT MATRIX

| Chain | Network ID | USDC Address | Verification |
|-------|-----------|-------------|--------------|
| Base | eip155:8453 | 0x833589...a02913 | PayAI facilitator |
| Solana | solana:5eykt4... | EPjFWdd5...TDt1v | PayAI facilitator |
| Ethereum | eip155:1 | 0xA0b869...eB48 | Self-verify |
| BSC | eip155:56 | 0x8AC76a...d580d | Self-verify |
| Arbitrum | eip155:42161 | 0xaf88d0...5831 | Self-verify |
| Optimism | eip155:10 | 0x0b2C63...Ff85 | Self-verify |
| Polygon | eip155:137 | 0x3c499c...3359 | Self-verify |

---

## CONNECTOR APIS — What data we have

| Connector | API Key | Status | Used By |
|-----------|---------|--------|---------|
| Helius | ✅ Working | Solana RPC, webhooks, transactions | wallet, cluster, whale, forensics |
| Etherscan | ✅ Working | Contract source, ABI, TX history | contract_inspect, tx_decoder |
| DeFiLlama | 🆓 Free | TVL, protocols, yields | protocol_research, yield_scanner |
| Birdeye | ✅ Working | Trending, token data | trending_tokens |
| CoinGecko | ✅ Working | Prices, categories, trending | market_price, market_sectors |
| DexScreener | 🆓 Free | Pairs, liquidity, volume | dex_activity, market_price |
| Moralis | ❌ Key invalid | Multi-chain wallet/token data | NOT USED |
| GMGN | ❌ Access denied | KOL tracking, trending | NOT USED |
| Arkham | ⚠️ Untested | Entity labeling | NOT USED |
| Nansen | ⚠️ Untested | Smart money, token god mode | NOT USED |
| Dune | ⚠️ Untested | Custom queries | NOT USED |

---

## TESTING

```bash
# Test all 59 tools (expect 403 = x402 enforcement working):
bash /root/backend/scripts/test_all_tools.sh

# Status dashboard:
python3 /root/scripts/rmi-status

# Pre-commit check:
bash /root/backend/scripts/pre-commit.sh
```

---

## COMMON ISSUES & FIXES

| Issue | Symptom | Fix |
|-------|---------|-----|
| Gateway files missing | Catalog shows 0 tools | Clone gateways to `/root/backend/x402-gateway/` |
| Tool returns 403 | x402 enforcement active | Expected — tools require payment or trial |
| Catalog stale after adding tools | Old count | Restart backend: `docker restart rmi-backend` |
| Payment verification fails | 402 responses | Check USDC addresses, network config |
| Self-signed cert on external | curl fails without -k | Cloudflare provides edge cert — use -k or browser |

---

## WHEN ADDING NEW TOOLS

1. Add definition to `x402-gateway/base/index.ts` (and solana if applicable)
2. Add route handler to `x402_tools.py`:
   ```python
   @router.post("/my_new_tool")
   async def my_new_tool(req: SomeRequest):
       """Description of what this tool does."""
       # Implementation using existing connectors
   ```
3. Restart backend: `docker restart rmi-backend`
4. Verify: `curl http://localhost:8000/api/v1/x402-tools/my_new_tool`
5. Check catalog auto-updated: `curl http://localhost:8000/api/v1/x402/tools-catalog`
