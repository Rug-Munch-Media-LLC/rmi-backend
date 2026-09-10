# MCP Directory Submissions — Complete Tracking

> Status of RMI listings across all MCP discovery directories and registries.

---

## Listed Directories

| # | Directory | URL | Status | Last Updated |
|---|-----------|-----|--------|-------------|
| 1 | **Smithery** | [smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence](https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence) | ✅ Listed | 2026-05-25 |
| 2 | **Glama** | [glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence](https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence) | ⏳ Submitted | 2026-05-25 |
| 3 | **mcp.so** | [mcp.so/server/rug-munch-intelligence](https://mcp.so/server/rug-munch-intelligence) | ⏳ Pending | 2026-05-25 |

## Pending Submissions

| # | Directory | URL | Method | Priority |
|---|-----------|-----|--------|----------|
| 4 | PulseMCP | pulsemcp.com | Web submit | High |
| 5 | MCP List | mcplist.ai | Web submit | High |
| 6 | FindMCP | findmcp.dev | Web submit | High |
| 7 | Official MCP Registry | github.com/modelcontextprotocol/servers | GitHub PR | Medium |
| 8 | Cline MCP Marketplace | github.com/cline/mcp-marketplace | GitHub PR | Medium |
| 9 | Open WebUI | openwebui.com | Community listing | Medium |
| 10 | LobeHub | lobehub.com | MCP listing | Medium |
| 11 | MCP Repository | mcprepository.com | Web submit | Low |
| 12 | Cursor Directory | cursor.directory | Plugin listing | Medium |
| 13 | Agentpedia | agentpedia.codes | MCP listing | Low |
| 14 | Microsoft MCP Center | mcp.microsoft.com | Enterprise | Low |
| 15 | Composio | composio.dev | Registry | Medium |
| 16 | mcp.run | mcp.run | Registry | Low |
| 17 | Awesome MCP Servers | github.com/punkpeye/awesome-mcp-servers | GitHub PR | Medium |

---

## Submission Information

Use these details for all directory submissions:

```
Name: Rug Munch Intelligence
Short Description: 70+ crypto security engines across 16 chains. Scam detection, wallet forensics, whale tracking, contract auditing, market analysis. Free trials + x402 micropayments. 8 payment facilitators. AI-native MCP server.
MCP Endpoint: https://rugmunch.io/mcp
MCP Transport: HTTP (Streamable)
Discovery: https://rugmunch.io/.well-known/mcp.json
x402 Discovery: https://rugmunch.io/.well-known/x402
GitHub: https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp
Website: https://rugmunch.io
Documentation: https://rugmunch.io/docs/mcp
Logo: https://rugmunch.io/logo.png
Contact: mcp@rugmunch.io
Maintainer: @cryptorugmuncher
Organization: CryptoRugMunch / Rug Munch Media LLC
License: Proprietary

Categories: Security, Intelligence, Market, Analysis, Social, Launchpad, Forensics, DeFi, NFT
Chains: Solana, Base, Ethereum, BSC, Arbitrum, Optimism, Polygon, Avalanche, Fantom, Gnosis, TRON, Bitcoin, SEPA
Pricing: Free trials (1-5 calls/tool). $0.01-$0.40/call via x402. Pay with USDC, USDT, BTC, EUR on 13 payment rails.
Facilitators: Coinbase CDP, PayAI, Cloudflare x402, EIP-7702, TRON Self-Verify, Bitcoin Self-Verify, AsterPay, x402-rs
```

### Tags (comma-separated)

```
crypto, blockchain, web3, mcp, security, intelligence, defi, scam-detection, whale-tracking, contract-audit, solana, ethereum, base, forensics, ai-agents, x402, micropayments, rug-pull, honeypot-detection, wallet-analysis, market-intelligence
```

---

## GitHub PR Submissions

### Official MCP Registry (modelcontextprotocol/servers)
- **Repo**: https://github.com/modelcontextprotocol/servers
- **Method**: Fork → Add entry → PR
- **Entry format**: JSON in `servers/` directory
- **PR title**: `Add Rug Munch Intelligence MCP server`

### Cline MCP Marketplace
- **Repo**: https://github.com/cline/mcp-marketplace
- **Method**: Fork → Add to `servers.json` → PR

### Awesome MCP Servers
- **Repo**: https://github.com/punkpeye/awesome-mcp-servers
- **Method**: Fork → Add to README → PR

---

## Verification Checklist

Before submitting to each directory, verify:

- [x] MCP endpoint responds at `https://rugmunch.io/mcp`
- [x] Discovery endpoint returns valid JSON at `https://rugmunch.io/.well-known/mcp.json`
- [x] x402 discovery returns payment info at `https://rugmunch.io/.well-known/x402`
- [x] GitHub repo is public and has smithery.json + glama.json in root
- [x] Logo accessible at `https://rugmunch.io/logo.png`
- [x] Documentation page live at `https://rugmunch.io/docs/mcp`
- [x] Free trials work without authentication
- [x] All 70+ security engines appear in discovery endpoint
- [x] Server responds to MCP `tools/list` method
- [x] Smithery listing is live and verified
- [ ] Glama listing is live and verified
- [ ] mcp.so listing submitted

---

## Post-Submission Monitoring

After listing on each directory:

1. **Verify listing** within 24 hours
2. **Test MCP connection** from the directory's "Try it" feature
3. **Check ratings/reviews** weekly
4. **Update descriptions** when tool count or features change
5. **Respond to user feedback** within 48 hours

---

*Last updated: 2026-05-25*
