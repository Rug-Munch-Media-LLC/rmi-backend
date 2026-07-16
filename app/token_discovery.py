"""
Token Launch Discovery - DexScreener + GeckoTerminal + GoPlus Security
=======================================================================
Multi-chain real-time token discovery. Add this route to main.py:
    from app.token_discovery import discover_endpoint
    app.include_router(discover_endpoint)
"""

from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

MONITORED_CHAINS = {
    "solana": "solana",
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
}


async def fetch_dexscreener_new_pairs(chain: str, limit: int = 20) -> list[dict]:
    """Fetch newest trading pairs from DexScreener."""
    url = "https://api.dexscreener.com/latest/dex/search"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, params={"q": chain, "limit": limit})
            resp.raise_for_status()
            pairs = resp.json().get("pairs", [])
            now = datetime.now(UTC)
            recent = []
            for pair in pairs:
                created = pair.get("pairCreatedAt")
                if created:
                    age = (
                        now - datetime.fromtimestamp(created / 1000, tz=UTC)
                    ).total_seconds() / 3600
                    if age <= 24:
                        recent.append(pair)
            logger.info(f"DexScreener: {len(recent)} recent pairs on {chain}")
            return recent
        except Exception as e:
            logger.error(f"DexScreener {chain}: {e}")
            return []


async def fetch_gecko_new_pools(network: str, limit: int = 20) -> list[dict]:
    """Fetch newest pools from GeckoTerminal."""
    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, params={"limit": limit})
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.warning(f"GeckoTerminal {network}: {e}")
            return []


async def scan_token_security(chain_id: str, token_address: str) -> dict | None:
    """GoPlus Security scan."""
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, params={"contract_addresses": token_address})
            resp.raise_for_status()
            result = resp.json().get("result", {}).get(token_address.lower(), {})
            if result:
                score = 0
                if result.get("is_honeypot") == "1":
                    score += 50
                if result.get("can_take_back_ownership") == "1":
                    score += 20
                if result.get("is_open_source") == "0":
                    score += 15
                if result.get("is_blacklisted") == "1":
                    score += 40
                buy_tax = float(result.get("buy_tax", "0"))
                sell_tax = float(result.get("sell_tax", "0"))
                if buy_tax > 10:
                    score += 15
                if sell_tax > 10:
                    score += 15
                if buy_tax > 50 or sell_tax > 50:
                    score += 20
                if result.get("is_proxy") == "1":
                    score += 10
                if result.get("is_mintable") == "1":
                    score += 10

                return {
                    "honeypot": result.get("is_honeypot") == "1",
                    "buy_tax": buy_tax,
                    "sell_tax": sell_tax,
                    "risk_score": min(100, max(0, score)),
                    "scanned_at": datetime.now(UTC).isoformat(),
                }
        except Exception as e:
            logger.warning(f"GoPlus {token_address}: {e}")
    return None


async def discover_tokens(chains: list[str] | None = None) -> dict:
    """Discover new tokens across chains. Returns {chain: [tokens]}."""
    if chains is None:
        chains = list(MONITORED_CHAINS.keys())

    result = {}
    for chain in chains:
        MONITORED_CHAINS.get(chain, chain)
        tokens = []
        seen = set()

        # DexScreener
        pairs = await fetch_dexscreener_new_pairs(chain)
        for pair in pairs:
            base = pair.get("baseToken", {})
            addr = base.get("address", "").lower()
            if addr and addr not in seen:
                seen.add(addr)
                tokens.append(
                    {
                        "address": addr,
                        "name": base.get("name", ""),
                        "symbol": base.get("symbol", ""),
                        "chain": chain,
                        "dex": pair.get("dexId", ""),
                        "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0)),
                        "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                        "price_usd": float(pair.get("priceUsd", 0)),
                        "created_at": datetime.fromtimestamp(
                            pair.get("pairCreatedAt", 0) / 1000, tz=UTC
                        ).isoformat()
                        if pair.get("pairCreatedAt")
                        else None,
                        "source": "dexscreener",
                    }
                )

        # GeckoTerminal
        pools = await fetch_gecko_new_pools(chain)
        for pool in pools:
            attrs = pool.get("attributes", {})
            rel = pool.get("relationships", {})
            base_data = rel.get("base_token", {}).get("data", {})
            addr = base_data.get("id", "").lower()
            if addr and addr not in seen:
                seen.add(addr)
                tokens.append(
                    {
                        "address": addr,
                        "name": attrs.get("name", ""),
                        "symbol": "",
                        "chain": chain,
                        "liquidity_usd": float(attrs.get("reserve_in_usd", 0)),
                        "price_usd": float(attrs.get("base_token_price_usd", 0)),
                        "source": "geckoterminal",
                    }
                )

        result[chain] = tokens

    return result
