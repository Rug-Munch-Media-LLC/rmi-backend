"""
Pump & Dump — Data Source Fetching
====================================
API helpers for DexScreener, Birdeye, and Helius.
"""

from __future__ import annotations

import json
import os

import httpx
import requests

from app.chain_registry import is_solana
from app.core.logging import get_logger
from app.domains.detectors.pump_dump_detector_package.types import BIRDEYE_API, DEXSCREENER_API

logger = get_logger("pump_dump_detector")


async def fetch_pairs(client: httpx.AsyncClient, token_address: str, chain: str) -> dict:
    """Fetch trading pair data from DexScreener."""
    try:
        resp = await client.get(
            f"{DEXSCREENER_API}/tokens/{token_address}",
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except (
        httpx.HTTPError,
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ConnectionError,
        OSError,
        TimeoutError,
    ) as e:
        logger.debug("fetch_pairs_error", exc_info=True)
        logger.warning(f"DexScreener fetch failed: {e}")
    return {}


async def fetch_trades(
    client: httpx.AsyncClient,
    token_address: str,
    chain: str,
    limit: int = 100,
) -> list[dict]:
    """Fetch recent trades from DexScreener or Birdeye."""
    trades: list[dict] = []
    try:
        if is_solana(chain):
            resp = await client.get(
                f"{BIRDEYE_API}/defi/txs/token",
                params={"address": token_address, "limit": limit},
                headers={"x-api-key": os.getenv("BIRDEYE_API_KEY", "")},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                trades = data.get("data", {}).get("txs", []) if data.get("success") else []
        else:
            resp = await client.get(f"{DEXSCREENER_API}/token-profiles/latest/v1", timeout=10)
    except (
        httpx.HTTPError,
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ConnectionError,
        OSError,
        TimeoutError,
    ) as e:
        logger.debug("fetch_trades_error", exc_info=True)
        logger.warning(f"Trade fetch failed: {e}")
    return trades


async def fetch_holders(
    client: httpx.AsyncClient,
    token_address: str,
    chain: str,
) -> dict:
    """Fetch holder data from available APIs."""
    try:
        if is_solana(chain):
            resp = await client.get(
                f"{BIRDEYE_API}/defi/token_overview",
                params={"address": token_address},
                headers={"x-api-key": os.getenv("BIRDEYE_API_KEY", "")},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("data"):
                    return {
                        "holders": data["data"].get("holder", 0),
                        "total_supply": data["data"].get("supply", 0),
                    }
    except (
        httpx.HTTPError,
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ConnectionError,
        OSError,
        TimeoutError,
    ) as e:
        logger.debug("fetch_holders_error", exc_info=True)
        logger.warning(f"Holder fetch failed: {e}")
    return {}
