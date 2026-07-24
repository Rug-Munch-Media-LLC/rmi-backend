"""
Token Supply Analyzer — API fetch helpers and data-parsing functions.

Handles DexScreener and Birdeye API calls with rate limiting and URL validation.
"""

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.token_supply_analyzer.models import (
    LockStatus,
    TokenSupplyProfile,
    _validate_url,
    _normalize_address,
    KNOWN_BURN_ADDRESSES,
    MAX_HOLDER_SAMPLE,
)

logger = get_logger("token_supply_analyzer")

# API endpoints
DEXSCREENER_TOKENS = "https://api.dexscreener.com/latest/dex/tokens/{token_addr}"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={}"
BIRDEYE_TOKEN = "https://public-api.birdeye.so/defi/v3/token/overview?address={token_addr}"
BIRDEYE_HOLDERS = "https://public-api.birdeye.so/defi/v3/token/holder?address={token_addr}"
SOLSCAN_TOKEN = "https://api.solscan.io/token/meta?token={token_addr}"
SOLSCAN_HOLDERS = "https://api.solscan.io/token/holders?token={token_addr}&limit=10"
ETHERSCAN_TOKEN = "https://api.etherscan.io/api?module=account&action=tokeninfo&contractaddress={token_addr}"
BSCSCAN_TOKEN = "https://api.bscscan.com/api?module=account&action=tokeninfo&contractaddress={token_addr}"

_RATE_LIMITERS: dict[str, float] = {}
_RATE_LIMIT_WINDOW = 0.5


async def _rate_limit_host(host: str) -> None:
    """Simple per-host rate limiter."""
    now = time.time()
    last = _RATE_LIMITERS.get(host, 0.0)
    wait = _RATE_LIMIT_WINDOW - (now - last)
    if wait > 0:
        await asyncio.sleep(wait)
    _RATE_LIMITERS[host] = time.time()


async def _fetch_json(url: str, source: str) -> dict[str, Any]:
    """Fetch JSON from a URL with rate limiting."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    try:
        await _rate_limit_host(host)
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(
                url,
                headers={"User-Agent": "RMI-TokenSupplyAnalyzer/1.0", "Accept": "application/json"},
            ) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, dict) and "data" in data:
                    return cast("dict[str, Any]", data["data"])
                return cast("dict[str, Any]", data)
            elif resp.status == 429:
                logger.warning("Rate limited by %s, waiting 2s...", source)
                await asyncio.sleep(2)
                return {}
            else:
                logger.warning("%s returned status %d for %s", source, resp.status, url[:60])
                return {}
    except TimeoutError:
        logger.warning("Timeout fetching from %s: %s", source, url[:60])
        return {}
    except Exception as e:
        logger.warning("Error fetching from %s: %s", source, e)
        return {}


async def _fetch_dexscreener(token_address: str, chain: str) -> dict[str, Any]:
    """Fetch token data from DexScreener API."""
    url = DEXSCREENER_TOKENS.format(token_addr=token_address)
    if not _validate_url(url):
        logger.warning("Invalid DexScreener URL for %s", token_address[:16])
        return {}
    return await _fetch_json(url, "DexScreener")


async def _fetch_birdeye(token_address: str) -> dict[str, Any]:
    """Fetch token data from Birdeye API."""
    url = BIRDEYE_TOKEN.format(token_addr=token_address)
    if not _validate_url(url):
        logger.warning("Invalid Birdeye URL for %s", token_address[:16])
        return {}
    return await _fetch_json(url, "Birdeye")


def _compute_pair_age_days(created_at_unix: int) -> float:
    """Compute pair age in days from Unix timestamp (ms)."""
    if not created_at_unix:
        return 0.0
    try:
        created = datetime.fromtimestamp(created_at_unix / 1000, tz=UTC)
        now = datetime.now(tz=UTC)
        return max(0.0, (now - created).total_seconds() / 86400)
    except (ValueError, OSError):
        return 0.0


async def parse_dexscreener_data(profile: TokenSupplyProfile, dexscreener_data: dict) -> None:
    """Parse DexScreener data into the profile."""
    pairs = dexscreener_data.get("pairs", [])
    if not pairs:
        return
    pair = pairs[0]
    profile.symbol = pair.get("baseToken", {}).get("symbol", "")
    profile.name = pair.get("baseToken", {}).get("name", "")
    profile.decimals = int(pair.get("baseToken", {}).get("decimals", 18))
    profile.pair_liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0))
    profile.pair_age_days = _compute_pair_age_days(pair.get("pairCreatedAt", 0))

    if "buyTax" in pair:
        profile.buy_tax_pct = float(pair.get("buyTax", 0))
    if "sellTax" in pair:
        profile.sell_tax_pct = float(pair.get("sellTax", 0))

    lp_holders = pair.get("liquidity", {}).get("holders", [])
    if lp_holders:
        norm_owner = _normalize_address(profile.owner_address)
        norm_addr = _normalize_address(profile.address)
        for h in lp_holders:
            if _normalize_address(h.get("address", "")) in (norm_owner, norm_addr):
                profile.lp_token_pct_held_by_deployer = float(h.get("percent", 0))
                break


async def parse_birdeye_data(profile: TokenSupplyProfile, birdeye_data: dict) -> None:
    """Parse Birdeye data into the profile."""
    profile.total_supply_raw = int(birdeye_data.get("totalSupply", birdeye_data.get("supply", 0)))
    profile.circulating_supply_raw = int(birdeye_data.get("circulatingSupply", 0))
    profile.max_supply_raw = int(birdeye_data.get("maxSupply", 0))

    divider = 10**profile.decimals
    if profile.total_supply_raw > 0:
        profile.total_supply = profile.total_supply_raw / divider
    if profile.circulating_supply_raw > 0:
        profile.circulating_supply = profile.circulating_supply_raw / divider
    if profile.max_supply_raw > 0:
        profile.max_supply = profile.max_supply_raw / divider

    holders = birdeye_data.get("holders", [])
    if holders:
        profile.holder_count = len(holders)
        top10_pct = 0.0
        top50_pct = 0.0
        for i, h in enumerate(holders):
            pct = float(h.get("percent", h.get("percentage", 0)))
            if i < 10:
                top10_pct += pct
            if i < 50:
                top50_pct += pct
            if i < MAX_HOLDER_SAMPLE:
                profile.whale_holdings.append({
                    "address": h.get("address", ""), "pct": pct, "balance": h.get("balance", 0)
                })
        profile.top_10_holder_pct = top10_pct
        profile.top_50_holder_pct = top50_pct

        if profile.owner_address:
            norm_owner = _normalize_address(profile.owner_address)
            for h in holders:
                if _normalize_address(h.get("address", "")) == norm_owner:
                    profile.deployer_hold_pct = float(h.get("percent", h.get("percentage", 0)))
                    break

    profile.has_mint_function = birdeye_data.get("mintable", False)
    profile.has_burn_function = birdeye_data.get("burnable", False)
    profile.is_deflationary = birdeye_data.get("deflationary", False)

    lock_data = birdeye_data.get("liquidityLock", {})
    if lock_data:
        locked = lock_data.get("locked", False)
        expiry = lock_data.get("expiry", "")
        if locked:
            profile.lock_status = LockStatus.LOCKED
            profile.liquidity_lock_expiry = expiry
        elif lock_data.get("removed", False):
            profile.lock_status = LockStatus.REMOVED
        else:
            profile.lock_status = LockStatus.UNLOCKED

    profile.is_renounced = birdeye_data.get("renounced", False)
    profile.owner_address = birdeye_data.get("owner", "")
    profile.has_proxy_admin = birdeye_data.get("proxy", False)
    profile.has_multisig = birdeye_data.get("multisig", False)

    if not profile.buy_tax_pct:
        profile.buy_tax_pct = float(birdeye_data.get("buyTax", 0))
    if not profile.sell_tax_pct:
        profile.sell_tax_pct = float(birdeye_data.get("sellTax", 0))
    profile.has_dynamic_tax = birdeye_data.get("dynamicTax", False)
    profile.has_blacklist = birdeye_data.get("blacklist", False)
    profile.has_max_wallet_limit = birdeye_data.get("maxWallet", False)


__all__ = [
    "DEXSCREENER_TOKENS",
    "BIRDEYE_TOKEN",
    "_fetch_dexscreener",
    "_fetch_birdeye",
    "_fetch_json",
    "_compute_pair_age_days",
    "_rate_limit_host",
    "parse_dexscreener_data",
    "parse_birdeye_data",
]
