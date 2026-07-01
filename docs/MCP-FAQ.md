# Rug Munch Intelligence — Frequently Asked Questions

> Everything you need to know about crypto intelligence, MCP tools, x402 payments, and more.

---

## General

### What is Rug Munch Intelligence?

Rug Munch Intelligence (RMI) is an AI-powered crypto security platform that provides **210 tools** across **13 blockchains** for scam detection, rug pull prevention, wallet forensics, whale tracking, contract auditing, and market analysis. It's built for AI agents via the Model Context Protocol (MCP) and accessible to humans through our web platform.

### What is MCP?

The **Model Context Protocol** is an open standard that allows AI assistants (like Claude, Cursor, Windsurf, ChatGPT) to discover and call external tools. RMI implements MCP so any MCP-compatible agent can use our 210 crypto intelligence tools directly.

### How do I connect RMI to my AI assistant?

Add this to your MCP configuration:

**Claude Desktop / Cursor:**
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

**Windsurf / HTTP clients:**
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

### What blockchains are supported?

We support **13 chains**: Solana, Base, Ethereum, BSC (BNB Chain), Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis, TRON, Bitcoin, and SEPA (European bank transfers).

### How many tools are available?

**210 tools** across multiple categories: Security (29 + 9 SENTINEL), Intelligence (27), Market (15), Analysis (14), Social (11), Launchpad (7), Premium (7), DeFi (4), NFT (2), Bundles (4), API (3), and Variants (80 per-chain).

### What endpoint formats are available?

Six discovery and tool-format endpoints are available:

| Endpoint | Format | Description |
|:---|:---|:---|
| `GET /api/v1/x402-tools/discovery` | x402 v2 | Full x402 protocol discovery with payment metadata |
| `GET /api/v1/x402-tools/catalog` | JSON | Human-readable organized tool catalog |
| `GET /api/v1/x402-tools/openai-tools` | OpenAI | OpenAI function calling format (210 tools) |
| `GET /api/v1/x402-tools/anthropic-tools` | Anthropic | Anthropic tool use format (210 tools) |
| `GET /api/v1/x402-tools/gemini-tools` | Gemini | Google Gemini function declarations (210 tools) |
| `GET /api/v1/x402-tools/langchain-tools` | LangChain | LangChain tool schema format (210 tools) |

All return the same 210 tools in their respective formats.

### What are the SENTINEL modules?

SENTINEL is our deep scanning suite — 9 specialized modules that can run individually ($0.05–$0.08 each) or as a full parallel scan ($0.15):

| Module | Price | Description |
|:---|:---|:---|
| `holder_analysis` | $0.05 | HHI concentration, fake diversification detection |
| `bundle_detect` | $0.08 | Bundle/sniper detection, funding chain analysis |
| `exchange_fund_check` | $0.05 | CEX-funded wallet detection |
| `liquidity_verify` | $0.05 | Lock verification, fake locker detection |
| `dev_reputation` | $0.05 | Serial rugg detection, cross-chain dev tracking |
| `wash_trading` (SENTINEL) | $0.08 | Circular transfer detection, cross-DEX loops |
| `metadata_fingerprint` | $0.05 | HTML structure hashing, description similarity |
| `pumpfun_analysis` | $0.08 | Bonding curve, bot detection (Solana only) |
| `sentiment_check` | $0.05 | Social sentiment scoring, bot campaign detection |
| `sentinel_scan` (all 9) | $0.15 | Full parallel deep scan with graded risk score |

---

## Pricing & Payments

### Is it free to try?

**Yes!** Every tool offers 1–5 free trial calls. No wallet required — trials are gated by device fingerprint. Just start calling tools and your trials are automatically applied.

### What happens after free trials?

After trials expire, you need to pay per call via **x402 micropayments** ($0.01–$0.40/call). Payment is automatic when using an MCP client with wallet support.

### What is x402?

x402 is an open micropayment protocol that enables per-call crypto payments for API access. Instead of monthly subscriptions, you pay fractions of a cent per tool call, settled on-chain.

### Which cryptocurrencies do you accept?

| Currency | Chains |
|----------|--------|
| USDC | Base, Solana, Ethereum, BSC, Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis |
| USDT | TRON, BSC |
| USDD | TRON |
| BTC | Bitcoin (1-confirmation) |
| EUR | SEPA (European bank transfer via AsterPay) |

### Which wallets can I use?

- **MetaMask** — EVM chains (Base, Ethereum, BSC, Arbitrum, Optimism, Polygon, Avalanche, etc.)
- **Phantom** — Solana + EVM chains
- **Solflare** — Solana
- **Backpack** — Solana
- **Coinbase Wallet** — Base, Ethereum
- **Rainbow** — EVM chains
- **TronLink** — TRON
- **WalletConnect** — Any compatible wallet

### What are payment facilitators?

We use **8 facilitators** to process payments across different chains:

1. **Coinbase CDP** — Fee-free USDC on Base & Solana
2. **PayAI** — Base & Solana USDC with deferred settlement
3. **Cloudflare x402** — Base Sepolia & Ethereum fallback
4. **EIP-7702** — Universal EVM (BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base)
5. **TRON Self-Verify** — Fee-free USDT/USDC/USDD on TRON
6. **Bitcoin Self-Verify** — Fee-free BTC via Mempool.space
7. **AsterPay** — EUR/SEPA European off-ramp
8. **x402-rs** — Self-hosted Docker facilitator

### Do you offer refunds?

**Yes.** Full refund if a tool returns no data. Request within 48 hours by posting to `/api/v1/x402/refund` with your transaction hash. No questions asked on empty-result refunds.

### Can I get a refund if the data was wrong?

We refund for **no data returned**. If data is returned but you disagree with the analysis, that falls under our accuracy policy — contact support@mcp.rugmunch.io.

---

## Tools & Features

### What can the security tools detect?

- **Honeypots** — Buy-only mechanics, 99% sell tax, blacklist traps
- **Rug pull predictors** — AI scoring with 12+ on-chain signals
- **Clone detectors** — Bytecode similarity against 10,000+ known scam contracts
- **Sniper detectors** — Bot activity in first blocks after launch
- **MEV alerts** — Sandwich attacks, frontrunning, backrunning
- **Wash trading** — Artificial volume inflation detection

### How accurate is the rug pull predictor?

Our predictor uses 12+ on-chain signals including liquidity lock status, ownership concentration, holder distribution, deployer history, mint authority, and social signals. Risk levels: Low / Medium / High / Critical.

### What chains do security tools support?

All 13 supported chains. High-value tools (audit, rug pull predictor) support EVM chains. Solana-specific tools handle SPL token analysis. TRON and Bitcoin tools are chain-specific.

### Can I use multiple tools in one call?

Yes! **Bundle endpoints** combine multiple tools into single calls for efficiency. Available bundles: `unified_scan`, `security_bundle`, `intelligence_bundle`, `full_audit`.

### What is the difference between free trial and paid calls?

| Feature | Free Trial | Paid |
|---------|-----------|------|
| Calls per tool | 1–5 | Unlimited |
| Rate limit | 60/hr | 300/hr |
| Wallet required | No | Yes |
| Data depth | Standard | Full |
| Response time | Normal | Priority |

---

## Technical

### How does device fingerprinting work?

Trials are gated by a device fingerprint (browser + IP hash). Each unique fingerprint gets 1–5 free calls per tool. Connecting a wallet unlocks extended trials (3 per standard tool, 1 per premium).

### What's the x402 payment flow?

1. Agent calls a tool endpoint
2. If trials expired, server returns `402 Payment Required` with payment details
3. Agent client signs an EIP-3009 (USDC) or similar authorization
4. Payment is verified on-chain by the facilitator
5. Tool response is returned with payment receipt

### Is the MCP endpoint streaming or request-response?

We support both **HTTP** and **SSE** transports. The primary endpoint (`https://rugmunch.io/mcp`) uses streamable HTTP. Legacy SSE is available at `https://rugmunch.io/mcp/sse`.

### What's the rate limit?

- **Trial**: 60 requests/hour per fingerprint
- **x402 paid**: 300 requests/hour per wallet
- **Enterprise**: Custom — contact mcp@rugmunch.io

### How do I check my trial balance?

```
GET /api/v1/x402-tools/trials?fingerprint=<your-fingerprint-id>
```

Returns per-tool trial counts: used, remaining, and maximum.

---

## Listing & Discovery

### Where can I find RMI listed?

- **Smithery**: [smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence](https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence)
- **Glama**: [glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence](https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence)
- **mcp.so**: [mcp.so/server/rug-munch-intelligence](https://mcp.so/server/rug-munch-intelligence)
- **GitHub**: [github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp](https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp)

### How do I list RMI in my MCP client?

Use our discovery endpoint:
```
GET https://rugmunch.io/.well-known/mcp.json
```

This returns the full server metadata with capabilities, stats, and connection info.

---

## Troubleshooting

### I'm getting a 402 error

This means your free trials have expired. You need to either:
1. Connect a wallet for extended trials
2. Send an x402 payment header with your request
3. Use a different device fingerprint (not recommended — we detect abuse)

### Tool returned no data

If a tool returns empty/null results, you're eligible for a **full refund**. Post to `/api/v1/x402/refund` with your transaction hash within 48 hours.

### My wallet isn't connecting

Make sure you're using a supported wallet (MetaMask, Phantom, Solflare, etc.) and that your wallet is connected to the correct chain. For EVM tools, switch to Base or Ethereum. For Solana tools, use Phantom/Solflare.

### I hit the rate limit

Wait an hour for the window to reset. If you need higher limits, contact mcp@rugmunch.io for enterprise pricing.

---

## Contact & Support

- **Website**: https://rugmunch.io
- **Documentation**: https://rugmunch.io/docs/mcp
- **Email**: mcp@rugmunch.io
- **GitHub**: https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp
- **Twitter**: @CryptoRugMunch
