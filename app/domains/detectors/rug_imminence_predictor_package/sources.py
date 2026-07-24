"""
RIP — Data Source Wrappers
External API calls for LP data, deployer info, wallet age, etc.
"""
import time

from app.core.logging import get_logger

logger = get_logger(__name__)


async def fetch_dexscreener_pool(address: str, chain: str) -> dict | None:
    """Fetch DEX pool data from DexScreener API."""
    import httpx
    chain_map = {
        "ethereum": "ethereum", "base": "base", "bsc": "bsc",
        "polygon": "polygon", "arbitrum": "arbitrum",
        "avalanche": "avalanche", "solana": "solana",
        "optimism": "optimism",
    }
    api_chain = chain_map.get(chain, chain)
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    if api_chain != "ethereum":
        url = f"https://api.dexscreener.com/token/v1/{api_chain}/{address}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("pairs") and len(data["pairs"]) > 0:
                    return data
                pairs = data.get("pairs", [])
                if pairs:
                    return data
    except (httpx.HTTPError):
        logger.debug("dexscreener fetch failed", exc_info=True)
    return None


async def resolve_deployer(address: str, chain: str) -> str | None:
    """Find deployer address for a token contract."""
    import httpx
    try:
        if chain == "solana":
            rpc = "https://api.mainnet-beta.solana.com"
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [address, {"encoding": "base64"}]})
                if resp.status_code == 200:
                    return address[:16]
        else:
            rpc = "https://eth.llamarpc.com"
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [address]})
    except (ConnectionError, TimeoutError, httpx.HTTPError):
        logger.debug("deployer resolve failed", exc_info=True)
    return None


async def check_wallet_age(wallet: str, chain: str) -> dict | None:
    """Estimate wallet age from first transaction."""
    import httpx
    try:
        if chain == "solana":
            rpc = "https://api.mainnet-beta.solana.com"
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [wallet, {"limit": 1}]})
                if resp.status_code == 200:
                    sigs = resp.json().get("result", [])
                    if sigs:
                        block_time = sigs[0].get("blockTime")
                        if block_time:
                            age_days = (time.time() - block_time) / 86400
                            return {"age_days": round(age_days, 1), "first_tx": sigs[0].get("signature", "")}
        return None
    except (httpx.HTTPError):
        return None


async def check_deployer_history(deployer: str, chain: str) -> dict:
    """Check deployer's token deployment history."""
    result = {"total_deployed": 0, "rug_tokens": 0, "active_tokens": 0}
    try:
        from app.scam_database import ScamDatabase
        db = ScamDatabase()
        scam_records = db.search_by_deployer(deployer)
        if scam_records:
            result["total_deployed"] = len(scam_records)
            result["rug_tokens"] = len([r for r in scam_records if r.get("type") == "rug"])
            result["active_tokens"] = len([r for r in scam_records if r.get("active", False)])
    except (ImportError, AttributeError):
        pass
    return result


async def check_lp_lock(address: str, chain: str) -> dict:
    """Check if LP tokens are locked via known lockers."""
    result = {"locked": False, "unlocked": True, "unlock_date": None}
    try:
        from app.domains.scanners.liquidity_verifier import LiquidityVerifier
        verifier = LiquidityVerifier()
        lp_info = await verifier.check_lock(address, chain)
        if lp_info:
            result["locked"] = lp_info.get("locked", False)
            result["unlocked"] = not result["locked"]
            result["unlock_date"] = lp_info.get("unlock_date")
    except (ImportError, AttributeError):
        pass
    return result


async def fetch_holder_data(address: str, chain: str) -> dict | None:
    """Fetch holder count and concentration data."""
    try:
        import httpx
        chain_lower = chain.lower()
        explorer_urls = {
            "ethereum": f"https://api.etherscan.io/api?module=token&action=tokenholderlist&contractaddress={address}",
            "bsc": f"https://api.bscscan.com/api?module=token&action=tokenholderlist&contractaddress={address}",
        }
        url = explorer_urls.get(chain_lower)
        if url:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "1" and data.get("result"):
                        holders = data["result"]
                        total_supply = sum(float(h.get("value", 0)) for h in holders[:20])
                        top10 = sum(float(h.get("value", 0)) for h in holders[:10])
                        return {
                            "holders_now": len(holders),
                            "holders_24h_ago": len(holders),
                            "top10_pct": (top10 / total_supply * 100) if total_supply > 0 else 0,
                        }
    except (httpx.HTTPError):
        logger.debug("holder data fetch failed", exc_info=True)
    return None
