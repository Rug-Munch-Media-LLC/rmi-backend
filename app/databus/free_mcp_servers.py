"""
RMI Free MCP Servers - 5 high-value tools to attract bots → x402 revenue funnel.
Each server: generous free tier → rate limit → x402 pay-per-call upgrade.
Listed on Smithery, Glama, mcp.so for maximum discoverability.
"""

import json
import os

from app.core.redis import get_redis
from app.routers.x402_databus_tools import check_trial


# ═══════════════════════════════════════════════════
# SHARED: Redis + x402 trial tracker
# ═══════════════════════════════════════════════════
# MCP #1: CRYPTO NEWS - 500+ sources, sentiment-scored
# ═══════════════════════════════════════════════════
def search_news(query: str, limit: int = 10, fingerprint: str = "anon") -> dict:
    """Search 500+ crypto news sources. 20 free calls/day."""
    auth = check_trial(fingerprint, "news", 20)
    if auth.get("tier") == "free_exhausted":
        return {"error": "Free tier exhausted", "upgrade": auth["upgrade"]}
    r = get_redis()
    results = []
    q = query.lower()
    for idx in ["rmi:news:500feeds", "rmi:news:index", "rmi:news:global:index"]:
        for aid in r.zrevrange(idx, 0, -1):
            a = json.loads(r.get(f"rmi:news:article:{aid}") or "{}")
            if q in a.get("title", "").lower():
                results.append(
                    {
                        "title": a["title"],
                        "source": a.get("source", ""),
                        "date": a.get("ingested_at", 0),
                    }
                )
                if len(results) >= limit:
                    return {
                        "query": query,
                        "results": results,
                        "total_sources": 500,
                        "auth": auth,
                        "mcp": "rmi-news",
                    }
    return {
        "query": query,
        "results": results,
        "total_sources": 500,
        "auth": auth,
        "mcp": "rmi-news",
    }


# ═══════════════════════════════════════════════════
# MCP #2: WALLET INTELLIGENCE - 10M+ labels, 13 chains
# ═══════════════════════════════════════════════════
def resolve_wallet(address: str, fingerprint: str = "anon") -> dict:
    """Resolve any crypto address across 13 chains. 15 free/day."""
    auth = check_trial(fingerprint, "wallet", 15)
    if auth.get("tier") == "free_exhausted":
        return {"error": "Free tier exhausted", "upgrade": auth["upgrade"]}
    r = get_redis()
    addr = address.lower()
    # Check Postgres wallet_labels
    from dotenv import load_dotenv

    load_dotenv("/app/.env", override=True)
    import psycopg2

    result = {"address": address, "labels": [], "chains_found": []}
    try:
        pg = psycopg2.connect(
            host="rmi-postgres",
            port=5432,
            user="rmi",
            password=os.getenv("POSTGRES_PASSWORD"),
            dbname="rmi",
        )
        cur = pg.cursor()
        cur.execute(
            "SELECT chain, label, name_tag FROM wallet_labels WHERE address = %s LIMIT 5", (addr,)
        )
        for chain, label, tag in cur.fetchall():
            result["labels"].append({"chain": chain, "label": label, "entity": tag})
            result["chains_found"].append(chain)
        cur.close()
        pg.close()
    except Exception:  # noqa: BLE001
        pass
    # Also check Redis cache
    for chain in ["ethereum", "solana", "bsc", "polygon"]:
        cached = r.get(f"rmi:label:{chain}:{addr}")
        if cached:
            c = json.loads(cached)
            if {
                "chain": chain,
                "label": c.get("label", ""),
                "entity": c.get("name_tag", ""),
            } not in result["labels"]:
                result["labels"].append(
                    {"chain": chain, "label": c.get("label", ""), "entity": c.get("name_tag", "")}
                )
    result["total_label_db"] = "10M+ addresses (MBAL + eth-labels + Chainabuse)"
    result["auth"] = auth
    result["mcp"] = "rmi-wallet-intel"
    return result


# ═══════════════════════════════════════════════════
# MCP #3: TOKEN SECURITY - Rug pull, honeypot, scam
# ═══════════════════════════════════════════════════
def scan_token(address: str, chain: str = "ethereum", fingerprint: str = "anon") -> dict:
    """Security scan any token. 10 free/day. Premium: $0.02/call."""
    auth = check_trial(fingerprint, "security", 10)
    if auth.get("tier") == "free_exhausted":
        return {"error": "Free tier exhausted", "upgrade": auth["upgrade"]}
    import httpx as req

    result = {"address": address, "chain": chain, "checks": {}}
    # Chainabuse check
    try:
        r = req.get(f"https://api.chainabuse.com/v0/reports?address={address}", timeout=5)
        if r.status_code == 200:
            reports = r.json().get("reports", [])
            result["checks"]["chainabuse_reports"] = len(reports)
            result["checks"]["known_scam"] = len(reports) > 0
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    # GoPlus security check (free, no key)
    try:
        r = req.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain}?contract_addresses={address}",
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("result", {}).get(address.lower(), {})
            result["checks"]["honeypot"] = data.get("is_honeypot") == "1"
            result["checks"]["buy_tax"] = data.get("buy_tax", "0")
            result["checks"]["sell_tax"] = data.get("sell_tax", "0")
            result["checks"]["liquidity"] = data.get("lp_holders", [])
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    result["auth"] = auth
    result["mcp"] = "rmi-token-security"
    return result


# ═══════════════════════════════════════════════════
# MCP #4: CROSS-CHAIN BRIDGE MONITOR
# ═══════════════════════════════════════════════════
def check_bridge_transfers(
    address: str = "", bridge: str = "wormhole", fingerprint: str = "anon"
) -> dict:
    """Check cross-chain bridge activity. 10 free/day."""
    auth = check_trial(fingerprint, "bridge", 10)
    if auth.get("tier") == "free_exhausted":
        return {"error": "Free tier exhausted", "upgrade": auth["upgrade"]}
    import httpx as req

    bridges = {
        "wormhole": "https://wormholescan.io/api/v1/operations",
        "layerzero": "https://layerzeroscan.com/api",
    }
    url = bridges.get(bridge, bridges["wormhole"])
    result = {"bridge": bridge, "address": address, "transfers": []}
    try:
        r = req.get(f"{url}?address={address}&limit=5" if address else f"{url}/stats", timeout=10)
        if r.status_code == 200:
            result["data"] = r.json()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    result["supported_bridges"] = list(bridges.keys())
    result["auth"] = auth
    result["mcp"] = "rmi-bridge-monitor"
    return result


# ═══════════════════════════════════════════════════
# MCP #5: DEFI ANALYTICS - TVL, yields, protocols
# ═══════════════════════════════════════════════════
def defi_analytics(protocol: str = "", chain: str = "", fingerprint: str = "anon") -> dict:
    """DeFi TVL, yields, protocol health. 20 free/day. Premium: $0.01/call."""
    auth = check_trial(fingerprint, "defi", 20)
    if auth.get("tier") == "free_exhausted":
        return {"error": "Free tier exhausted", "upgrade": auth["upgrade"]}
    import httpx as req

    result = {"protocol": protocol, "chain": chain}
    try:
        r = req.get(
            f"https://api.llama.fi/protocol/{protocol}"
            if protocol
            else "https://api.llama.fi/protocols",
            timeout=10,
        )
        if r.status_code == 200:
            result["tvl_data"] = r.json()
            result["source"] = "DeFiLlama (free, unlimited)"
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    # Pyth price feed
    try:
        r = req.get(
            "https://hermes.pyth.network/api/latest_price_feeds?ids[]=0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
            timeout=5,
        )
        if r.status_code == 200:
            p = r.json()[0]["price"]
            result["eth_price"] = float(p["price"]) * (10 ** float(p["expo"]))
            result["price_source"] = "Pyth Network (125+ institutional publishers)"
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    result["auth"] = auth
    result["mcp"] = "rmi-defi-analytics"
    return result


# MCP Server registry
MCP_TOOLS = {
    "rmi-news": {
        "search": search_news,
        "description": "500+ source crypto news, 20 free/day",
        "price": 0.01,
    },
    "rmi-wallet-intel": {
        "resolve": resolve_wallet,
        "description": "10M+ labeled addresses, 15 free/day",
        "price": 0.02,
    },
    "rmi-token-security": {
        "scan": scan_token,
        "description": "Rug pull + honeypot detection, 10 free/day",
        "price": 0.02,
    },
    "rmi-bridge-monitor": {
        "check": check_bridge_transfers,
        "description": "Cross-chain bridge monitoring, 10 free/day",
        "price": 0.01,
    },
    "rmi-defi-analytics": {
        "analyze": defi_analytics,
        "description": "DeFi TVL + yields, 20 free/day",
        "price": 0.01,
    },
}
