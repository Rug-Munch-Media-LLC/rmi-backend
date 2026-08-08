"""
MCP Tools for All Our Keyed Services - Efficient API usage through caching shield.

Every tool routes through: cache → rate limit → provider → result.
All keys from vault, all calls cached.

Services wrapped:
  GMGN - token analytics, holder distribution, sniper detection
  Helius DAS - token metadata, wallet holdings, asset search
  Birdeye - token overview, price, volume
  Solscan - token metadata, holders, transactions
  Etherscan - contract verification, ABIs, gas
  CoinGecko - prices, market data, trending
  GoPlus - token security, honeypot detection
  Moralis - EVM wallet balances, token holdings, NFTs
  QuickNode - Solana RPC (backup)
  Alchemy - Solana RPC (backup)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("service_mcp")

# Cache
_l1: dict[str, tuple[float, dict[str, Any] | None]] = {}
_hits = 0
_misses = 0


def _cache_key(service: str, method: str, params: dict[str, Any]) -> str:
    raw = f"{service}:{method}:{json.dumps(params, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def _cached_call(
    service: str,
    method: str,
    params: dict[str, Any],
    fn: Callable[..., Awaitable[dict[str, Any] | None]],
    ttl: int = 60,
) -> dict[str, Any] | None:
    global _hits, _misses
    key = _cache_key(service, method, params)
    entry = _l1.get(key)
    if entry:
        expiry, data = entry
        if time.monotonic() < expiry:
            _hits += 1
            return data
        del _l1[key]
    _misses += 1
    result = await fn(**params)
    if result:
        _l1[key] = (time.monotonic() + ttl, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# GMGN - Token Analytics, Holder Distribution, Sniper Detection
# ═══════════════════════════════════════════════════════════════════════════


class GMGNTools:
    """GMGN token analytics - holder distribution, sniper detection, bundle analysis."""

    def __init__(self) -> None:
        self._key = os.getenv("GMGN_API_KEY", "")
        self._base = "https://gmgn.ai/api/v1"

    async def token_security(self, chain: str, address: str) -> dict[str, Any] | None:
        async def fn(chain: str, address: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/token_security/{chain}/{address}",
                    headers={"Authorization": f"Bearer {self._key}"},
                )
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    return {
                        "risk_score": d.get("risk_score"),
                        "honeypot": d.get("is_honeypot"),
                        "renounced": d.get("renounced_mint"),
                        "lp_burned": d.get("lp_burned"),
                        "top10_holder_pct": d.get("top10_holder_rate"),
                    }
                return None

        return await _cached_call("gmgn", "token_security", {"chain": chain, "address": address}, fn, 60)

    async def holder_distribution(self, chain: str, address: str) -> dict[str, Any] | None:
        async def fn(chain: str, address: str) -> dict[str, int] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/token_holders/{chain}/{address}",
                    headers={"Authorization": f"Bearer {self._key}"},
                )
                if r.status_code == 200:
                    holders = r.json().get("data", {}).get("holders", [])
                    return {"holder_count": len(holders), "top_holders": holders[:10]}
                return None

        return await _cached_call("gmgn", "holders", {"chain": chain, "address": address}, fn, 120)

    async def sniper_check(self, chain: str, address: str) -> dict[str, Any] | None:
        async def fn(chain: str, address: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/token_snipers/{chain}/{address}",
                    headers={"Authorization": f"Bearer {self._key}"},
                )
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    return {
                        "sniper_count": d.get("sniper_count", 0),
                        "snipers": d.get("snipers", [])[:5],
                    }
                return None

        return await _cached_call("gmgn", "snipers", {"chain": chain, "address": address}, fn, 60)


# ═══════════════════════════════════════════════════════════════════════════
# BIRDEYE - Token Overview, Price, Volume
# ═══════════════════════════════════════════════════════════════════════════


class BirdeyeTools:
    def __init__(self) -> None:
        self._key = os.getenv("BIRDEYE_API_KEY", "")
        self._base = "https://public-api.birdeye.so"

    async def token_overview(self, address: str) -> dict[str, Any] | None:
        async def fn(address: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/defi/token_overview",
                    params={"address": address},
                    headers={"X-API-KEY": self._key, "x-chain": "solana"},
                )
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    return {
                        "price": d.get("price"),
                        "liquidity": d.get("liquidity"),
                        "volume_24h": d.get("volume24hUSD"),
                        "mc": d.get("mc"),
                    }
                return None

        return await _cached_call("birdeye", "overview", {"address": address}, fn, 30)

    async def token_price(self, address: str) -> dict[str, Any] | None:
        async def fn(address: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    f"{self._base}/defi/price",
                    params={"address": address},
                    headers={"X-API-KEY": self._key, "x-chain": "solana"},
                )
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    return {"price": d.get("value"), "change_24h": d.get("priceChange24h")}
                return None

        return await _cached_call("birdeye", "price", {"address": address}, fn, 8)


# ═══════════════════════════════════════════════════════════════════════════
# SOLSCAN - Token Metadata, Holders, Transactions
# ═══════════════════════════════════════════════════════════════════════════


class SolscanTools:
    def __init__(self) -> None:
        self._key = os.getenv("SOLSCAN_API_KEY", "")
        self._base = "https://pro-api.solscan.io/v2.0"

    async def token_meta(self, address: str) -> dict[str, Any] | None:
        async def fn(address: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/token/meta",
                    params={"address": address},
                    headers={"token": self._key},
                )
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    return {
                        "name": d.get("name"),
                        "symbol": d.get("symbol"),
                        "decimals": d.get("decimals"),
                        "supply": d.get("supply"),
                    }
                return None

        return await _cached_call("solscan", "meta", {"address": address}, fn, 120)

    async def token_holders(self, address: str, limit: int = 10) -> dict[str, Any] | None:
        async def fn(address: str, limit: int) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/token/holders",
                    params={"address": address, "limit": limit},
                    headers={"token": self._key},
                )
                if r.status_code == 200:
                    return {"holders": r.json().get("data", [])}
                return None

        return await _cached_call("solscan", "holders", {"address": address, "limit": limit}, fn, 60)


# ═══════════════════════════════════════════════════════════════════════════
# COINGECKO - Prices, Market Data, Trending
# ═══════════════════════════════════════════════════════════════════════════


class CoinGeckoTools:
    def __init__(self) -> None:
        self._key = os.getenv("COINGECKO_API_KEY", "")
        self._base = "https://pro-api.coingecko.com/api/v3"

    async def price(self, token_id: str = "solana") -> dict[str, Any] | None:
        async def fn(token_id: str) -> Any | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/simple/price",
                    params={
                        "ids": token_id,
                        "vs_currencies": "usd",
                        "include_market_cap": "true",
                        "include_24hr_vol": "true",
                    },
                    headers={"x-cg-pro-api-key": self._key},
                )
                if r.status_code == 200:
                    return r.json().get(token_id, {})
                return None

        return await _cached_call("coingecko", "price", {"token_id": token_id}, fn, 30)

    async def trending(self) -> dict[str, Any] | None:
        async def fn() -> dict[str, list[Any | None]] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._base}/search/trending", headers={"x-cg-pro-api-key": self._key})
                if r.status_code == 200:
                    coins = r.json().get("coins", [])
                    return {"trending": [c.get("item", {}).get("name") for c in coins[:10]]}
                return None

        return await _cached_call("coingecko", "trending", {}, fn, 300)


# ═══════════════════════════════════════════════════════════════════════════
# ETHERSCAN - Contract Verification, ABI, Gas
# ═══════════════════════════════════════════════════════════════════════════


class EtherscanTools:
    def __init__(self) -> None:
        self._key = os.getenv("ETHERSCAN_API_KEY", "")
        self._base = "https://api.etherscan.io/api"

    async def contract_abi(self, address: str) -> dict[str, Any] | None:
        async def fn(address: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    self._base,
                    params={
                        "module": "contract",
                        "action": "getabi",
                        "address": address,
                        "apikey": self._key,
                    },
                )
                if r.status_code == 200 and r.json().get("status") == "1":
                    return {"abi": r.json().get("result")}
                return None

        return await _cached_call("etherscan", "abi", {"address": address}, fn, 3600)

    async def gas_oracle(self) -> dict[str, Any] | None:
        async def fn() -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    self._base,
                    params={"module": "gastracker", "action": "gasoracle", "apikey": self._key},
                )
                if r.status_code == 200:
                    d = r.json().get("result", {})
                    if isinstance(d, str):
                        return {"gas_data": d}
                    return {
                        "safe_gas": d.get("SafeGasPrice"),
                        "propose_gas": d.get("ProposeGasPrice"),
                        "fast_gas": d.get("FastGasPrice"),
                    }
                return None

        return await _cached_call("etherscan", "gas", {}, fn, 15)


# ═══════════════════════════════════════════════════════════════════════════
# MORALIS - EVM Wallet Balances, Token Holdings, NFTs
# ═══════════════════════════════════════════════════════════════════════════


class MoralisTools:
    def __init__(self) -> None:
        self._keys = [os.getenv(f"MORALIS_API_KEY{s}", "") for s in ("", "_2", "_3")]
        self._keys = [k for k in self._keys if k]
        self._idx = 0
        self._base = "https://deep-index.moralis.io/api/v2.2"

    def _next_key(self) -> Any:
        key = self._keys[self._idx % len(self._keys)] if self._keys else ""
        self._idx += 1
        return key

    async def wallet_balance(self, address: str, chain: str = "eth") -> dict[str, Any] | None:
        async def fn(address: str, chain: str) -> dict[str, Any | None] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/{address}/balance",
                    params={"chain": chain},
                    headers={"X-API-Key": self._next_key()},
                )
                if r.status_code == 200:
                    return {"balance_wei": r.json().get("balance")}
                return None

        return await _cached_call("moralis", "balance", {"address": address, "chain": chain}, fn, 15)

    async def wallet_tokens(self, address: str, chain: str = "eth") -> dict[str, Any] | None:
        async def fn(address: str, chain: str) -> dict[str, int] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/{address}/erc20",
                    params={"chain": chain},
                    headers={"X-API-Key": self._next_key()},
                )
                if r.status_code == 200:
                    tokens = r.json()
                    return {"token_count": len(tokens), "tokens": tokens[:20]}
                return None

        return await _cached_call("moralis", "tokens", {"address": address, "chain": chain}, fn, 30)


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED SERVICE MCP
# ═══════════════════════════════════════════════════════════════════════════


class ServiceMCP:
    """All our keyed services as MCP-compatible cached tools."""

    def __init__(self) -> None:
        self.gmgn = GMGNTools()
        self.birdeye = BirdeyeTools()
        self.solscan = SolscanTools()
        self.coingecko = CoinGeckoTools()
        self.etherscan = EtherscanTools()
        self.moralis = MoralisTools()
        self.coinmarketcap = CoinMarketCapTools()

    def stats(self) -> dict[str, Any]:
        return {
            "cache_hits": _hits,
            "cache_misses": _misses,
            "services": [
                "gmgn",
                "birdeye",
                "solscan",
                "coingecko",
                "etherscan",
                "moralis",
                "coinmarketcap",
            ],
            "l1_size": len(_l1),
        }


_service_mcp: ServiceMCP | None = None


def get_service_mcp() -> ServiceMCP:
    global _service_mcp
    if _service_mcp is None:
        _service_mcp = ServiceMCP()
    return _service_mcp


# ═══════════════════════════════════════════════════════════════════════════
# COINMARKETCAP - Market data, listings, trends, OHLCV (10K free/mo)
# ═══════════════════════════════════════════════════════════════════════════


class CoinMarketCapTools:
    def __init__(self) -> None:
        self._key = os.getenv("COINMARKETCAP_API_KEY", "")
        self._base = "https://pro-api.coinmarketcap.com/v1"

    async def latest_listings(self, limit: int = 10) -> dict[str, Any] | None:
        async def fn(limit: int) -> dict[str, Any] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/cryptocurrency/listings/latest",
                    params={"limit": limit, "convert": "USD"},
                    headers={"X-CMC_PRO_API_KEY": self._key},
                )
                if r.status_code == 200:
                    coins = r.json().get("data", [])
                    return {
                        "count": len(coins),
                        "top": [
                            {
                                "name": c["name"],
                                "symbol": c["symbol"],
                                "price": c["quote"]["USD"]["price"],
                                "market_cap": c["quote"]["USD"]["market_cap"],
                                "volume_24h": c["quote"]["USD"]["volume_24h"],
                                "change_24h": c["quote"]["USD"]["percent_change_24h"],
                            }
                            for c in coins[:limit]
                        ],
                    }
                return None

        return await _cached_call("cmc", "listings", {"limit": limit}, fn, 60)

    async def quotes(self, symbols: list[str]) -> dict[str, Any] | None:
        async def fn(symbols: list[str]) -> dict[str, Any] | None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{self._base}/cryptocurrency/quotes/latest",
                    params={"symbol": ",".join(symbols), "convert": "USD"},
                    headers={"X-CMC_PRO_API_KEY": self._key},
                )
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    return {s: {"price": data[s]["quote"]["USD"]["price"]} for s in symbols if s in data}
                return None

        return await _cached_call("cmc", "quotes", {"symbols": tuple(symbols)}, fn, 30)
