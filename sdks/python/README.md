# rugmunch-sdk

Python SDK for [Rug Munch Intelligence](https://rugmunch.io) — sovereign crypto intelligence platform with 13+ chains, 96 data providers, 8 MCP tools, and x402 paid tier.

## Install

```bash
pip install rugmunch
```

## Quick start (async)

```python
import asyncio
from rugmunch_sdk import RMI

async def main():
    async with RMI(base_url="https://api.rugmunch.io", api_key="...") as client:
        # Real-time token risk (FREE 5/day, $0.01 thereafter)
        risk = await client.get_token_risk(chain="solana", address="DezXAZ...")
        print(f"Risk: {risk.score}/100 ({risk.tier})")

        # RAG search
        hits = await client.rag_search("ethereum wallet security", collection="scam_intel")
        for h in hits:
            print(f"  - {h.text[:80]}...")

        # Full AI research report ($5/report)
        report = await client.generate_report("token", "solana:DezXAZ...")
        print(report.markdown[:500])

asyncio.run(main())
```

## Quick start (sync — for scripts / notebooks)

```python
from rugmunch_sdk import RMI_sync

with RMI_sync(base_url="https://api.rugmunch.io") as client:
    risk = client.get_token_risk(chain="solana", address="DezXAZ...")
    print(f"Risk: {risk.score}/100 ({risk.tier})")
```

## Available methods

| Method | Free? | x402 price |
|--------|-------|------------|
| `get_health()` | yes | free |
| `get_catalog_stats()` | yes | free |
| `get_x402_catalog()` | yes | free |
| `list_mcp_tools()` | yes | free |
| `call_mcp_tool(name, args)` | per tool | per tool |
| `get_token_risk(chain, address)` | 5/day | $0.01 |
| `get_wallet_analysis(chain, address)` | 5/day | $0.01 |
| `resolve_entity(wallet_id)` | no | $0.10 |
| `rag_search(query, collection, top_k)` | 5/day | $0.01 |
| `rag_ingest(content, collection)` | no | $0.10 |
| `list_news(since_hours, limit)` | 5/day | $0.01 |
| `get_news_trending(window_hours, limit)` | 5/day | $0.01 |
| `get_news(news_id)` | 5/day | $0.01 |
| `analyze_news(news_id)` | 5/day | $0.01 |
| `generate_report(subject_type, subject_id)` | no | $5.00 |

## MCP integration

RMI is a Model Context Protocol server. Add to your AI agent:

```json
{
  "mcpServers": {
    "rugmunch": {
      "url": "https://api.rugmunch.io/mcp",
      "transport": "streamable-http"
    }
  }
}
```

## License

MIT © Rug Munch Intelligence
