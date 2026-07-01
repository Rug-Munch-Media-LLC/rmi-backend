# Rug Munch Intelligence — MCP Server

> **MCP server exposing crypto intelligence tools to AI agents.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-2024--11--05-blue.svg)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

8 tools for AI agents. Real-time token risk, wallet analysis, deployer reputation, news sentiment, full AI research reports, RAG search, similar tokens, and cross-chain entity resolution. 13+ chains (Solana, Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Tron, Bitcoin, Avalanche, Fantom, Gnosis + EVM subnets). x402 paid tier.

## Quick start

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "rugmunch": {
      "url": "https://mcp.rugmunch.io/mcp",
      "transport": "streamable-http"
    }
  }
}
```

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "rugmunch": {
      "url": "https://mcp.rugmunch.io/mcp",
      "transport": "streamable-http"
    }
  }
}
```

### Continue.dev

Add to `~/.continue/config.json`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "name": "rugmunch",
        "url": "https://mcp.rugmunch.io/mcp"
      }
    ]
  }
}
```

## Available tools

| Tool | Description | x402 price |
|------|-------------|------------|
| `get_token_risk` | Real-time risk score for any token across 13+ chains | FREE 5/day, $0.01 |
| `get_wallet_analysis` | Wallet activity, balance, history, reputation | FREE 5/day, $0.01 |
| `get_deployer_reputation` | Deployer reputation 0-100, deterministic | $0.02 |
| `get_news_sentiment` | Latest news + composite sentiment | FREE 5/day, $0.01 |
| `generate_report` | 7-section LLM research report | $5.00 |
| `query_catalog` | Natural language catalog query | $0.05 |
| `find_similar_tokens` | Vector-similar tokens | $0.03 |
| `resolve_entity` | Cross-chain entity resolution | $0.10 |

## Authentication

Free tier: 5 calls/day, no auth.
Paid tier: include `X-Payment: <base64(tx_hash+signature)>` header.

## Architecture

- **OpenAPI**: https://api.rugmunch.io/openapi.json
- **MCP server**: https://api.rugmunch.io/mcp (JSON-RPC 2.0 + plain JSON)
- **x402 catalog**: https://api.rugmunch.io/api/v1/x402/catalog
- **Health**: https://api.rugmunch.io/health

## Direct tool calls (no MCP)

```bash
# Get token risk
curl -X POST https://api.rugmunch.io/mcp/call/get_token_risk \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"chain":"solana","address":"DezXAZ..."}}'

# Generate AI report
curl -X POST https://api.rugmunch.io/mcp/call/generate_report \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"subject_type":"token","subject_id":"solana:DezXAZ..."}}'
```

## Python SDK

```bash
pip install rugmunch
```

```python
import asyncio
from rugmunch_sdk import RMI

async def main():
    async with RMI(base_url="https://api.rugmunch.io") as client:
        risk = await client.get_token_risk("solana", "DezXAZ...")
        print(f"Risk: {risk.score}/100 ({risk.tier})")
        report = await client.generate_report("token", "solana:DezXAZ...")
        print(report.markdown[:500])

asyncio.run(main())
```

## TypeScript SDK

```bash
npm install @rugmunch/sdk
```

```typescript
import { RMI } from "@rugmunch/sdk";
const client = new RMI({ baseUrl: "https://api.rugmunch.io" });
const risk = await client.getTokenRisk("solana", "DezXAZ...");
```

## License

MIT © Rug Munch Intelligence

## Links

- Homepage: https://rugmunch.io
- Documentation: https://docs.rugmunch.io
- Repository: https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp
- HuggingFace: https://huggingface.co/cryptorugmunch/rug-munch-intelligence
- Smithery: https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence
- Glama: https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence
- mcp.so: https://mcp.so/server/rug-munch-intelligence
