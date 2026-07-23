"""
Deep Intelligence Engine — Core Infrastructure
================================================
Configuration, multi-chain data client, and module-level instances.
"""

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("deep_intel")


# ── Configuration ──────────────────────────────────────────────────────────


class DeepIntelConfig:
    HELIUS_KEY: str = os.getenv("HELIUS_API_KEY", "")
    HELIUS_KEY_2: str = os.getenv("HELIUS_API_KEY_2", "")
    BIRDEYE_KEY: str = os.getenv("BIRDEYE_API_KEY", "")
    ETHERSCAN_KEY: str = os.getenv("ETHERSCAN_KEY", "")
    BSCSCAN_KEY: str = os.getenv("BSCSCAN_KEY", "")
    ALCHEMY_KEY: str = os.getenv("ALCHEMY_KEY", "")
    GMGN_KEY: str = os.getenv("GMGN_API_KEY", "")
    COINGECKO_KEY: str = os.getenv("COINGECKO_API_KEY", "")
    MORALIS_KEY: str = os.getenv("MORALIS_API_KEY", "")
    OPENROUTER_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    WEBACY_KEY: str = os.getenv("WEBACY_API_KEY", "")
    BITQUERY_KEY: str = os.getenv("BITQUERY_API_KEY", "")
    LUNARCRUSH_KEY: str = os.getenv("LUNARCRUSH_API_KEY", "")
    GOPLUS_KEY: str = os.getenv("GOPLUS_API_KEY", "")
    CHAINABUSE_KEY: str = os.getenv("CHAINABUSE_API_KEY", "")

    CHAINS = ["sol", "eth", "bsc", "base", "arb", "polygon", "avax"]
    CHAIN_RPC_MAP = {
        "eth": f"https://eth-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_KEY', '')}",
        "bsc": "https://bsc-dataseed.binance.org",
        "base": f"https://base-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_KEY', '')}",
        "arb": f"https://arb-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_KEY', '')}",
        "polygon": f"https://polygon-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_KEY', '')}",
    }
    CHAIN_SCAN_MAP = {
        "eth": ("https://api.etherscan.io/api", os.getenv("ETHERSCAN_KEY", "")),
        "bsc": ("https://api.bscscan.com/api", os.getenv("BSCSCAN_KEY", "")),
    }
    CHAIN_DEXSCREENER_MAP = {
        "sol": "solana",
        "eth": "ethereum",
        "bsc": "bsc",
        "base": "base",
        "arb": "arbitrum",
        "polygon": "polygon",
        "avax": "avalanche",
    }


cfg = DeepIntelConfig()


# ── Data Clients ───────────────────────────────────────────────────────────


class MultiChainClient:
    """Unified client for on-chain data across all chains"""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def close(self) -> None:
        await self.http.aclose()

    # ── GMGN ──

    async def gmgn_get(self, path: str) -> dict:
        try:
            headers = {"Content-Type": "application/json"}
            if cfg.GMGN_KEY:
                headers["X-API-Key"] = cfg.GMGN_KEY
            resp = await self.http.get(f"https://gmgn.ai/api/v1{path}", headers=headers)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"GMGN HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── DexScreener ──

    async def dexscreener(self, chain: str, address: str) -> dict:
        try:
            chain_id = cfg.CHAIN_DEXSCREENER_MAP.get(chain, chain)
            resp = await self.http.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                matching = [p for p in pairs if p.get("chainId") == chain_id]
                if matching:
                    best = max(
                        matching, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0)
                    )
                    return best
                return {"error": "no matching pair found"}
            return {"error": f"DexScreener HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Etherscan / BSCScan ──

    async def scan_get(self, chain: str, params: dict) -> dict:
        if chain not in cfg.CHAIN_SCAN_MAP:
            return {"error": f"no scanner for {chain}"}
        base_url, api_key = cfg.CHAIN_SCAN_MAP[chain]
        params["apikey"] = api_key
        try:
            resp = await self.http.get(base_url, params=params)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Scanner HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Helius (Solana) ──

    async def helius_get(self, path: str) -> dict:
        if not cfg.HELIUS_KEY:
            return {"error": "HELIUS_API_KEY not set"}
        try:
            resp = await self.http.get(f"https://api.helius.xyz/v0{path}?api-key={cfg.HELIUS_KEY}")
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Helius HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    async def helius_post(self, path: str, payload: dict) -> dict:
        if not cfg.HELIUS_KEY:
            return {"error": "HELIUS_API_KEY not set"}
        try:
            resp = await self.http.post(
                f"https://api.helius.xyz/v0{path}?api-key={cfg.HELIUS_KEY}", json=payload
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Helius HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Birdeye ──

    async def birdeye_get(self, path: str) -> dict:
        if not cfg.BIRDEYE_KEY:
            return {"error": "BIRDEYE_API_KEY not set"}
        try:
            resp = await self.http.get(
                f"https://public-api.birdeye.so{path}",
                headers={"X-API-KEY": cfg.BIRDEYE_KEY, "x-chain": "solana"},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Birdeye HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Moralis ──

    async def moralis_get(self, path: str, chain: str = "eth") -> dict:
        if not cfg.MORALIS_KEY:
            return {"error": "MORALIS_API_KEY not set"}
        chain_map = {
            "eth": "0x1",
            "bsc": "0x38",
            "base": "0x2105",
            "arb": "0xa4b1",
            "polygon": "0x89",
            "avax": "0xa86a",
        }
        try:
            resp = await self.http.get(
                f"https://deep-index.moralis.io/api/v2.2{path}",
                headers={"X-API-Key": cfg.MORALIS_KEY},
                params={"chain": chain_map.get(chain, chain)},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Moralis HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Webacy (Threat Intel) ──

    async def webacy_get(self, chain: str, address: str) -> dict:
        if not cfg.WEBACY_KEY:
            return {"error": "WEBACY_API_KEY not set"}
        try:
            chain_map = {
                "eth": "ethereum",
                "bsc": "bsc",
                "base": "base",
                "arb": "arbitrum",
                "polygon": "polygon",
                "avax": "avalanche",
                "sol": "solana",
            }
            webacy_chain = chain_map.get(chain, chain)
            resp = await self.http.get(
                f"https://api.webacy.com/api/v1/token/{webacy_chain}/{address}",
                headers={"x-api-key": cfg.WEBACY_KEY},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Webacy HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Bitquery v2 (GraphQL) ──

    async def bitquery_post(self, query: str, variables: dict | None = None) -> dict:
        if not cfg.BITQUERY_KEY:
            return {"error": "BITQUERY_API_KEY not set"}
        try:
            resp = await self.http.post(
                "https://graphql.bitquery.io/",
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {cfg.BITQUERY_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Bitquery HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── LunarCrush (Social Sentiment) ──

    async def lunarcrush_get(self, path: str, params: dict | None = None) -> dict:
        if not cfg.LUNARCRUSH_KEY:
            return {"error": "LUNARCRUSH_API_KEY not set"}
        try:
            resp = await self.http.get(
                f"https://lunarcrush.com/api4{path}",
                headers={"Authorization": f"Bearer {cfg.LUNARCRUSH_KEY}"},
                params=params or {},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"LunarCrush HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── GoPlus (On-Chain Security) ──

    async def goplus_get(self, chain: str, address: str) -> dict:
        if not cfg.GOPLUS_KEY:
            return {"error": "GOPLUS_API_KEY not set"}
        try:
            chain_map = {
                "eth": "1",
                "bsc": "56",
                "base": "8453",
                "arb": "42161",
                "polygon": "137",
                "avax": "43114",
                "sol": "999",
            }
            chain_id = chain_map.get(chain, "1")
            resp = await self.http.get(
                f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
                params={"contract_addresses": address},
                headers={"Authorization": f"Bearer {cfg.GOPLUS_KEY}"},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"GoPlus HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Chainabuse (Threat Intel) ──

    async def chainabuse_get(self, address: str) -> dict:
        if not cfg.CHAINABUSE_KEY:
            return {"error": "CHAINABUSE_API_KEY not set"}
        try:
            resp = await self.http.get(
                f"https://api.chainabuse.com/api/v1/address/{address}/reports",
                headers={"Authorization": f"Bearer {cfg.CHAINABUSE_KEY}"},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Chainabuse HTTP {resp.status_code}"}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}

    # ── Generic ──

    async def simple_get(self, url: str, headers: dict | None = None) -> dict:
        try:
            resp = await self.http.get(url, headers=headers or {})
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}", "url": url}
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"err: {e}")
            return {"error": str(e)}


# ── Module-level instance ──

client = MultiChainClient()
