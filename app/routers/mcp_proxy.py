"""
Local MCP Server Proxy — makes free local MCP servers accessible via REST API.
Reads tool definitions from MCP server manifests and proxies tool calls.

Local servers run via stdio — we spawn them on demand with a process cache.
All data is FREE to call (no API keys, just local compute/RPC queries).
"""

import asyncio
import json
import subprocess
import time

SERVER_REGISTRY = {
    "feargreed": {
        "name": "Crypto Fear & Greed Index",
        "path": "/root/.hermes/mcp-servers/crypto-feargreed-mcp",
        "command": ["python3", "main.py"],
        "tools": [
            {
                "id": "feargreed_get_index",
                "name": "Get Fear & Greed Index",
                "description": "Current crypto Fear & Greed Index value and classification",
            },
            {
                "id": "feargreed_get_historical",
                "name": "Get Historical Fear & Greed",
                "description": "Historical Fear & Greed values for a date range",
            },
        ],
    },
    "crypto-news": {
        "name": "Crypto News API",
        "path": "/root/.hermes/mcp-servers/crypto-news-api",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "news_get_latest",
                "name": "Get Latest News",
                "description": "Real-time crypto news from 12+ sources with AI sentiment",
            },
            {
                "id": "news_search",
                "name": "Search News",
                "description": "Search crypto news by keyword, source, or date range",
            },
            {
                "id": "news_sentiment",
                "name": "News Sentiment",
                "description": "Aggregated market sentiment from news sources",
            },
        ],
    },
    "indicators": {
        "name": "Crypto Indicators",
        "path": "/root/.hermes/mcp-servers/crypto-indicators-mcp",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "indicators_rsi",
                "name": "RSI Indicator",
                "description": "Relative Strength Index for any token",
            },
            {
                "id": "indicators_macd",
                "name": "MACD Indicator",
                "description": "Moving Average Convergence Divergence",
            },
            {
                "id": "indicators_bollinger",
                "name": "Bollinger Bands",
                "description": "Bollinger Bands volatility indicator",
            },
            {"id": "indicators_ema", "name": "EMA", "description": "Exponential Moving Average"},
            {"id": "indicators_sma", "name": "SMA", "description": "Simple Moving Average"},
        ],
    },
    "evmscope": {
        "name": "EVMScope Intelligence",
        "path": "/root/.hermes/mcp-servers/evmscope",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "evmscope_token_price",
                "name": "Token Price",
                "description": "Current token price across chains",
            },
            {
                "id": "evmscope_token_info",
                "name": "Token Info",
                "description": "Token metadata — name, symbol, decimals, supply",
            },
            {
                "id": "evmscope_token_holders",
                "name": "Token Holders",
                "description": "Token holder distribution and top holders",
            },
            {
                "id": "evmscope_honeypot",
                "name": "Honeypot Check",
                "description": "Check if a token is a honeypot scam",
            },
            {
                "id": "evmscope_whale_movements",
                "name": "Whale Movements",
                "description": "Recent large transactions for a token",
            },
            {
                "id": "evmscope_gas_price",
                "name": "Gas Price",
                "description": "Current gas prices across chains",
            },
            {
                "id": "evmscope_portfolio",
                "name": "Portfolio",
                "description": "Wallet portfolio with balances across chains",
            },
            {
                "id": "evmscope_bridge_routes",
                "name": "Bridge Routes",
                "description": "Optimal bridge routes between chains",
            },
            {
                "id": "evmscope_yield_rates",
                "name": "Yield Rates",
                "description": "Current DeFi yield rates across protocols",
            },
            {
                "id": "evmscope_swap_quote",
                "name": "Swap Quote",
                "description": "Best swap quote across DEX aggregators",
            },
            {
                "id": "evmscope_nft_info",
                "name": "NFT Info",
                "description": "NFT metadata and collection info",
            },
            {
                "id": "evmscope_contract_abi",
                "name": "Contract ABI",
                "description": "Fetch verified contract ABI",
            },
            {
                "id": "evmscope_decode_tx",
                "name": "Decode Transaction",
                "description": "Decode raw transaction data",
            },
        ],
    },
    "polymarket": {
        "name": "Polymarket Data",
        "path": "/root/.hermes/mcp-servers/graph-polymarket-mcp",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "polymarket_active_markets",
                "name": "Active Markets",
                "description": "Currently active prediction markets",
            },
            {
                "id": "polymarket_market_detail",
                "name": "Market Detail",
                "description": "Detailed market data — prices, volume, liquidity",
            },
            {
                "id": "polymarket_orderbook",
                "name": "Orderbook",
                "description": "Live orderbook for a market",
            },
            {
                "id": "polymarket_open_interest",
                "name": "Open Interest",
                "description": "Open interest across markets",
            },
            {
                "id": "polymarket_traders",
                "name": "Top Traders",
                "description": "Top traders by volume/profit",
            },
        ],
    },
    "prediction-markets": {
        "name": "Prediction Markets",
        "path": "/root/.hermes/mcp-servers/prediction-market-mcp",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "predmkt_polymarket",
                "name": "Polymarket Data",
                "description": "Polymarket markets, prices, and events",
            },
            {
                "id": "predmkt_kalshi",
                "name": "Kalshi Data",
                "description": "Kalshi prediction market data",
            },
            {
                "id": "predmkt_predictit",
                "name": "PredictIt Data",
                "description": "PredictIt market data",
            },
        ],
    },
    "web3-research": {
        "name": "Web3 Research",
        "path": "/root/.hermes/mcp-servers/web3-research-mcp",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "research_coingecko",
                "name": "CoinGecko Research",
                "description": "Deep CoinGecko data — prices, markets, exchanges",
            },
            {
                "id": "research_defillama",
                "name": "DeFiLlama Research",
                "description": "Protocol TVL, yields, chain metrics from DeFiLlama",
            },
            {
                "id": "research_combined",
                "name": "Combined Research",
                "description": "Multi-source research combining CoinGecko + DeFiLlama",
            },
        ],
    },
    "jupiter": {
        "name": "Jupiter DEX",
        "path": "/root/.hermes/mcp-servers/jupiter-mcp",
        "command": ["node", "dist/index.js"],
        "tools": [
            {
                "id": "jupiter_quote",
                "name": "Swap Quote",
                "description": "Best swap route and price quote on Solana",
            },
            {
                "id": "jupiter_price",
                "name": "Token Price",
                "description": "Current token price via Jupiter aggregator",
            },
            {
                "id": "jupiter_token_list",
                "name": "Token List",
                "description": "All verified tokens on Jupiter",
            },
        ],
    },
}

# Process cache — keep server processes alive for 5 min
_proc_cache: dict[str, tuple[subprocess.Popen, float]] = {}
_CACHE_TTL = 300  # 5 minutes


async def _get_or_spawn(server_id: str):
    """Get or spawn an MCP server process."""
    if server_id not in SERVER_REGISTRY:
        return None

    now = time.time()
    if server_id in _proc_cache:
        proc, created = _proc_cache[server_id]
        if now - created < _CACHE_TTL and proc.poll() is None:
            return proc

    cfg = SERVER_REGISTRY[server_id]
    try:
        proc = subprocess.Popen(
            cfg["command"],
            cwd=cfg["path"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _proc_cache[server_id] = (proc, now)
        # Give it a moment to initialize
        await asyncio.sleep(1)
        return proc
    except Exception:
        return None


async def call_local_mcp(server_id: str, tool_name: str, params: dict | None = None) -> dict:
    """Call a tool on a local MCP server."""
    proc = await _get_or_spawn(server_id)
    if not proc:
        return {"error": f"Server '{server_id}' not available"}

    # MCP JSON-RPC call
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": params or {},
        },
    }

    try:
        proc.stdin.write((json.dumps(request) + "\n").encode())
        proc.stdin.flush()
        # Read response line
        line = proc.stdout.readline()
        result = json.loads(line.decode())
        if "error" in result:
            return {"error": result["error"]}
        return {"result": result.get("result", result)}
    except Exception as e:
        return {"error": str(e)}


def get_local_mcp_tools() -> list[dict]:
    """Get all available local MCP tools."""
    tools = []
    for server_id, cfg in SERVER_REGISTRY.items():
        for tool in cfg["tools"]:
            tools.append(
                {
                    "server": server_id,
                    "server_name": cfg["name"],
                    **tool,
                }
            )
    return tools
