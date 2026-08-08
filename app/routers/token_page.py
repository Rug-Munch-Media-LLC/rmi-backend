"""
Unified Token Page Router
=========================
Combines RugCharts OHLCV, RugMaps graph, scanner results, and BBS discussion
into a single endpoint: GET /api/v1/token/{chain}/{address}
"""

from __future__ import annotations
from typing import Any

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger("token_page_router")

router = APIRouter(prefix="/api/v1/token", tags=["token"])

DEXSCREENER_BASE = "https://api.dexscreener.com"


async def _fetch_dexscreener_pair(chain: str, address: str) -> dict | None:
    """Fetch token info from DexScreener."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{DEXSCREENER_BASE}/tokens/v1/{chain}/{address}")
            if resp.status_code == 200:
                pairs = resp.json()
                if isinstance(pairs, list) and len(pairs) > 0:
                    return pairs[0]
    except Exception as e:
        logger.debug(f"DexScreener fetch failed for {chain}/{address}: {e}")
    return None


async def _get_bbs_posts_for_token(token_address: str, limit: int = 10) -> list[dict]:
    """Get recent BBS posts about a token by tag matching."""
    try:
        from app.bulletin_board.manager import BulletinBoardManager

        result = await BulletinBoardManager.list_posts(
            status="published",
            tags=[token_address],
            limit=limit,
            sort_by="published_at",
            sort_order="desc",
        )
        posts = result.get("posts", [])
        # Return public-safe versions
        safe_posts = []
        for p in posts:
            safe = {
                k: v
                for k, v in p.items()
                if k
                not in [
                    "author_id",
                    "author_email",
                    "edit_history",
                    "approved_by",
                    "notification_sent",
                    "version",
                ]
            }
            safe_posts.append(safe)
        return safe_posts
    except Exception as e:
        logger.debug(f"BBS fetch failed for {token_address}: {e}")
        return []


@router.get("/{chain}/{address}")
async def token_page(
    chain: str,
    address: str,
    include_discussions: bool = Query(True),
    discussion_limit: int = Query(10, ge=1, le=50),
) -> dict:
    """Unified token page that combines all token intelligence into one endpoint.

    Returns:
    - dex_data: Real-time pair data from DexScreener (price, volume, liquidity, txns)
    - bbs_posts: Recent community discussions about this token (tagged by address)
    - links: Deep links to RugCharts, RugMaps, Scanner for this token
    """
    pair = await _fetch_dexscreener_pair(chain, address)

    dex_data = None
    if pair:
        base_token = pair.get("baseToken", {})
        quote_token = pair.get("quoteToken", {})
        txns = pair.get("txns", {}).get("h24", {})
        volume = pair.get("volume", {})
        liquidity = pair.get("liquidity", {})
        price_change = pair.get("priceChange", {})

        dex_data = {
            "chain": chain,
            "dex": pair.get("dexId", "unknown"),
            "pair_address": pair.get("pairAddress", ""),
            "pair_created_at": pair.get("pairCreatedAt"),
            "base_token": {
                "address": base_token.get("address", address),
                "name": base_token.get("name", ""),
                "symbol": base_token.get("symbol", ""),
            },
            "quote_token": {
                "address": quote_token.get("address", ""),
                "name": quote_token.get("name", ""),
                "symbol": quote_token.get("symbol", ""),
            },
            "price_usd": float(pair.get("priceUsd") or 0),
            "price_change": {
                "h24": price_change.get("h24", 0),
                "h6": price_change.get("h6", 0),
                "h1": price_change.get("h1", 0),
            },
            "volume": {
                "h24": volume.get("h24", 0),
                "h6": volume.get("h6", 0),
                "h1": volume.get("h1", 0),
            },
            "liquidity": {
                "usd": liquidity.get("usd", 0),
                "base": liquidity.get("base", 0),
                "quote": liquidity.get("quote", 0),
            },
            "fdv": pair.get("fdv", 0),
            "market_cap": pair.get("marketCap", 0),
            "txns": {
                "buys_24h": txns.get("buys", 0),
                "sells_24h": txns.get("sells", 0),
            },
            "info": {
                "websites": pair.get("info", {}).get("websites", []),
                "socials": pair.get("info", {}).get("socials", []),
            },
            "labels": pair.get("labels", []),
        }

    # Fetch BBS discussions
    discussions = []
    if include_discussions:
        discussions = await _get_bbs_posts_for_token(address, discussion_limit)

    # Build deep links
    links = {
        "rugcharts": f"/rugcharts?token={address}&chain={chain}",
        "rugmaps": f"/rugmaps?address={address}&chain={chain}",
        "scanner": f"/scanner/token/{address}",
        "token_page": f"/token/{chain}/{address}",
        "explorer": _get_explorer_url(chain, address),
    }

    return {
        "chain": chain,
        "address": address,
        "dex_data": dex_data,
        "discussions": discussions,
        "discussion_count": len(discussions),
        "links": links,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def _get_explorer_url(chain: str, address: str) -> str:
    explorers = {
        "solana": "https://solscan.io/account/",
        "ethereum": "https://etherscan.io/address/",
        "base": "https://basescan.org/address/",
        "bsc": "https://bscscan.com/address/",
        "arbitrum": "https://arbiscan.io/address/",
        "optimism": "https://optimistic.etherscan.io/address/",
        "polygon": "https://polygonscan.com/address/",
    }
    base = explorers.get(chain, explorers["ethereum"])
    return base + address
