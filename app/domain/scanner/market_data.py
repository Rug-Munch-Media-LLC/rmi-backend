"""Market data fetchers - DexScreener, GoPlus, Solana RPC."""

from typing import Any

import httpx

from app.domain.scanner.models import SOLANA_RPC_ENDPOINTS

logger = __import__("app.core.logging", fromlist=["get_logger"]).get_logger(__name__)


async def _solana_get_mint_info(token_address: str) -> dict[str, Any]:
    """Get mint info from Solana RPC."""
    result = {
        "mint_authority": "unknown",
        "freeze_authority": "unknown",
        "decimals": None,
        "supply": None,
    }
    for rpc_url in SOLANA_RPC_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAccountInfo",
                        "params": [token_address, {"encoding": "jsonParsed"}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    val = (data.get("result") or {}).get("value")
                    if val:
                        parsed = (val.get("data") or {}).get("parsed", {})
                        if parsed.get("type") == "mint":
                            info = parsed.get("info", {})
                            result["mint_authority"] = info.get("mintAuthority", "unknown")
                            result["freeze_authority"] = info.get("freezeAuthority", "unknown")
                            result["decimals"] = info.get("decimals")
                            result["supply"] = info.get("supply")
                            break
        except Exception as e:
            logger.warning("solana_rpc_failed", url=rpc_url, error=str(e))
    return result


async def _dexscreener_lookup(token_address: str, chain: str, client: httpx.AsyncClient) -> list | None:
    """Look up token on DexScreener."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("pairs")
    except Exception as e:
        logger.warning("dexscreener_lookup_failed", token=token_address, chain=chain, error=str(e))
    return None


async def fetch_market_data(token_address: str, chain: str) -> dict[str, Any]:
    """Fetch market data from DexScreener and Solana RPC."""
    result = {"chain": chain, "token_address": token_address}

    async with httpx.AsyncClient(timeout=10.0) as client:
        pairs = await _dexscreener_lookup(token_address, chain, client)
        if pairs:
            result["pairs"] = pairs[:5]  # Top 5 pairs

    if chain == "solana":
        mint_info = await _solana_get_mint_info(token_address)
        result["solana_mint"] = mint_info

    return result


async def _simulate_trade(token_address: str, chain: str) -> dict[str, Any] | None:
    """Simulate a trade to check for honeypot/mint issues."""
    # Placeholder - actual simulation logic would be in simulation.py
    logger.info("simulate_trade_started", token=token_address, chain=chain)
    return None


async def _check_honeypot_is(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if token is a honeypot using IS platform."""
    logger.info("check_honeypot_is", token=token_address, chain=chain)
    return {"check": "honeypot_is", "result": "pending"}


async def _check_chainpatrol_asset(asset_type: str, asset_id: str) -> dict[str, Any] | None:
    """Check asset on ChainPatrol."""
    logger.info("check_chainpatrol", type=asset_type, id=asset_id)
    return {"check": "chainpatrol", "result": "pending"}


async def _check_defi_scanner(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on DeFi Scanner."""
    logger.info("check_defi_scanner", token=token_address, chain=chain)
    return {"check": "defi_scanner", "result": "pending"}
