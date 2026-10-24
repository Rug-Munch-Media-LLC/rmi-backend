"""Shared helpers for the RugMaps sub-routers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.errors import ServiceUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)


class RugMapsRequest(BaseModel):
    center_wallet: str
    depth: int = 2
    min_strength: float = 0.1


def _get_rm() -> Any:
    try:
        from app.domains.scanner.rugmaps import get_bubble_maps_pro

        return get_bubble_maps_pro()
    except ImportError as e:
        raise ServiceUnavailableError(f"Module unavailable: {e}") from e


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


def _format_currency(value: float) -> str:
    """Format a number as a compact currency string."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.2f}"


async def _fetch_dexscreener_token_info(token_address: str, chain: str = "solana") -> dict[str, Any] | None:
    """Fetch token info from DexScreener as fallback when holder graph is empty."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://api.dexscreener.com/tokens/v1/{chain}/{token_address}")
            if resp.status_code == 200:
                pairs = resp.json()
                if isinstance(pairs, list) and len(pairs) > 0:
                    return pairs[0]
            # Also try latest/dex/tokens
            resp2 = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            if resp2.status_code == 200:
                data = resp2.json()
                pairs2 = data.get("pairs", [])
                if pairs2:
                    return pairs2[0]
    except Exception as e:
        logger.debug(f"DexScreener fallback failed for {token_address}: {e}")
    return None


def _build_dexscreener_graph(pair_data: dict[str, Any], token_address: str, chain: str) -> dict[str, Any]:
    """Build a meaningful bubble map from DexScreener pair data when
    on-chain transaction data is unavailable (contracts, not wallets)."""

    nodes = []
    links = []

    # Center node — the token itself
    token_name = pair_data.get("baseToken", {}).get("name", token_address[:8])
    token_symbol = pair_data.get("baseToken", {}).get("symbol", "")
    price_usd = float(pair_data.get("priceUsd") or 0)
    liquidity_usd = float(pair_data.get("liquidity", {}).get("usd", 0))
    volume_24h = float(pair_data.get("volume", {}).get("h24", 0))
    txns_24h = pair_data.get("txns", {}).get("h24", {})
    buys_24h = int(txns_24h.get("buys", 0))
    sells_24h = int(txns_24h.get("sells", 0))
    fdv = float(pair_data.get("fdv", 0))
    pair_address = pair_data.get("pairAddress", "")
    dex = pair_data.get("dexId", "unknown")

    # Center node: the token
    center_node = {
        "id": token_address,
        "address": token_address,
        "type": "center",
        "label": f"{token_symbol or token_address[:6]}...{token_address[-4:]}",
        "x": 400.0,
        "y": 300.0,
        "radius": 40.0,
        "color": "#ff6b6b",
        "volume": volume_24h,
        "transactions": buys_24h + sells_24h,
        "risk_score": 0,
        "risk_level": "unknown",
        "explorer_url": _get_explorer_url(chain, token_address),
        "metadata": {
            "name": token_name,
            "symbol": token_symbol,
            "price_usd": price_usd,
            "fdv": fdv,
            "liquidity_usd": liquidity_usd,
            "volume_24h": volume_24h,
        },
    }
    nodes.append(center_node)

    # Pair/dex node: the liquidity pool
    if pair_address:
        dex_node = {
            "id": pair_address,
            "address": pair_address,
            "type": "exchange",
            "label": f"{dex[:8]} Pool",
            "x": 320.0,
            "y": 240.0,
            "radius": 30.0,
            "color": "#4dabf7",
            "volume": liquidity_usd,
            "transactions": 0,
            "risk_score": 0,
            "risk_level": "safe",
            "explorer_url": _get_explorer_url(chain, pair_address),
            "metadata": {"dex": dex, "liquidity_usd": liquidity_usd},
        }
        nodes.append(dex_node)

        # Link: token → pool
        link_strength = min(liquidity_usd / 1_000_000, 1.0) if liquidity_usd > 0 else 0.3
        links.append(
            {
                "source": token_address,
                "target": pair_address,
                "strength": round(link_strength, 3),
                "width": max(1.0, link_strength * 5),
                "color": "#4dabf7",
                "volume": liquidity_usd,
                "transactions": 1,
            }
        )

    # Trading activity node: buy/sell volume
    if buys_24h > 0 or sells_24h > 0:
        tx_id = f"tx_{token_address[:8]}"
        ratio = buys_24h / max(sells_24h, 1)
        is_bullish = ratio > 1.2
        tx_node = {
            "id": tx_id,
            "address": tx_id,
            "type": "defi_trader",
            "label": f"Buys: {buys_24h} / Sells: {sells_24h}",
            "x": 480.0,
            "y": 260.0,
            "radius": 25.0,
            "color": "#20c997" if is_bullish else "#ff8787",
            "volume": volume_24h,
            "transactions": buys_24h + sells_24h,
            "risk_score": 0,
            "risk_level": "low",
            "explorer_url": "",
            "metadata": {
                "buys_24h": buys_24h,
                "sells_24h": sells_24h,
                "buy_sell_ratio": round(ratio, 2),
            },
        }
        nodes.append(tx_node)

        links.append(
            {
                "source": token_address,
                "target": tx_id,
                "strength": 0.6,
                "width": 3.0,
                "color": "#20c997" if is_bullish else "#ff8787",
                "volume": volume_24h,
                "transactions": buys_24h + sells_24h,
            }
        )

    # Volume info node
    if volume_24h > 0:
        vol_id = f"vol_{token_address[:8]}"
        vol_node = {
            "id": vol_id,
            "address": vol_id,
            "type": "whale",
            "label": f"Vol: ${_format_currency(volume_24h)}",
            "x": 400.0,
            "y": 360.0,
            "radius": 22.0,
            "color": "#ffd43b",
            "volume": volume_24h,
            "transactions": 0,
            "risk_score": 0,
            "risk_level": "low",
            "explorer_url": "",
            "metadata": {"volume_24h": volume_24h},
        }
        nodes.append(vol_node)

        links.append(
            {
                "source": token_address,
                "target": vol_id,
                "strength": min(volume_24h / 5_000_000, 1.0) if volume_24h > 0 else 0.2,
                "width": 2.0,
                "color": "#ffd43b",
                "volume": volume_24h,
                "transactions": 0,
            }
        )

    return {
        "nodes": nodes,
        "links": links,
        "node_count": len(nodes),
        "link_count": len(links),
        "dex_data": {
            "name": token_name,
            "symbol": token_symbol,
            "price_usd": price_usd,
            "fdv": fdv,
            "liquidity_usd": liquidity_usd,
            "volume_24h": volume_24h,
            "buys_24h": buys_24h,
            "sells_24h": sells_24h,
            "dex": dex,
            "pair_address": pair_address,
            "chain": chain,
        },
        "source": "dexscreener",
    }


# risk_flag → (category, severity, title, description)
_RISK_FLAG_META: dict[str, tuple[str, str, str, str]] = {
    "honeypot": ("contract", "critical", "Honeypot detected", "Sells are blocked — you can buy but not exit."),
    "flagged_spam": ("contract", "high", "Flagged as spam", "Token is flagged by spam heuristics."),
    "sanctioned_address": (
        "contract",
        "critical",
        "Sanctioned address involved",
        "Token interacts with a sanctioned address.",
    ),
    "bundled_launch": ("trading", "high", "Bundled launch", "Initial buys were bundled by the deployer."),
    "copycat_token": ("contract", "high", "Copycat token", "Mimics a well-known token's name/symbol."),
    "token_under_24h": ("trading", "medium", "Token under 24h old", "Extremely young token — elevated rug risk."),
    "extreme_lp_concentration": (
        "liquidity",
        "critical",
        "Extreme LP concentration",
        "Liquidity is concentrated in few wallets.",
    ),
    "concentrated_lp": ("liquidity", "high", "Concentrated LP", "Liquidity providers are highly concentrated."),
    "active_proxy_owner": (
        "contract",
        "critical",
        "Active proxy owner",
        "Proxy contract owner can upgrade the implementation.",
    ),
    "active_owner": ("contract", "high", "Active contract owner", "Owner privileges are still live on the contract."),
    "suspicious_social_sentiment": (
        "social",
        "medium",
        "Suspicious social sentiment",
        "Community signals indicate coordinated promotion.",
    ),
    "wash_trading_suspected": ("trading", "high", "Wash trading suspected", "Volume pattern indicates wash trading."),
    "extreme_volume_liquidity_ratio": (
        "trading",
        "high",
        "Extreme volume/liquidity ratio",
        "Volume far exceeds liquidity — likely artificial.",
    ),
}

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _flags_to_red_flags(risk_flags: list[str]) -> list[dict[str, Any]]:
    """Map SENTINEL risk-flag strings to structured red flags for the UI."""
    out: list[dict[str, Any]] = []
    for flag in risk_flags:
        meta = _RISK_FLAG_META.get(flag)
        if meta:
            category, severity, title, description = meta
        else:
            category, severity = "trading", "medium"
            title = flag.replace("_", " ").title()
            description = ""
        out.append(
            {
                "id": flag,
                "category": category,
                "severity": severity,
                "title": title,
                "description": description,
            }
        )
    out.sort(key=lambda f: -_SEVERITY_ORDER.get(str(f["severity"]), 0))
    return out


async def _run_token_risk_scan(token: str, chain: str) -> dict[str, Any]:
    """Run the SENTINEL token scan (free tier) and shape it for the panels.

    Cluster/bundle enrichment (``clusters`` block) runs CONCURRENTLY with the
    scan and can never block or fail it: ``fetch_bundle_block`` never raises and
    reports its own ``null_reasons`` when evidence is unavailable.
    """
    import asyncio

    from app.domains.scanner.service import scan_token as sentinel_scan
    from app.routers.rugmaps_router.bundle_block_fetch import fetch_bundle_block

    cluster_task = asyncio.ensure_future(fetch_bundle_block(token, chain))
    try:
        result = await asyncio.wait_for(
            sentinel_scan(token_address=token, chain=chain, tiers=["free"]),
            timeout=25.0,
        )
    except Exception as e:
        logger.warning(f"rugmaps scan failed for {token[:12]}: {e}")
        return {
            "token": token,
            "chain": chain,
            "risk_score": None,
            "risk_level": "unknown",
            "safety_score": None,
            "red_flags": [],
            "confidence": 0,
            "status": "scan_failed",
            "error": str(e)[:200],
            "clusters": await cluster_task,
        }

    safety = int(getattr(result, "safety_score", 50) or 50)
    risk_score = max(0, min(100, 100 - safety))
    if risk_score >= 70:
        risk_level = "critical"
    elif risk_score >= 40:
        risk_level = "high"
    elif safety >= 70:
        risk_level = "low"
    else:
        risk_level = "medium"

    return {
        "token": token,
        "chain": chain,
        "symbol": getattr(result, "symbol", "") or None,
        "name": getattr(result, "name", "") or None,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "safety_score": safety,
        "red_flags": _flags_to_red_flags(list(getattr(result, "risk_flags", []) or [])),
        "confidence": int(getattr(result, "confidence", 0) or 0),
        "modules_run": [
            {"module": m.get("module"), "status": m.get("status")} for m in (getattr(result, "modules_run", None) or [])
        ],
        "scanned_at": getattr(result, "scanned_at", None),
        "status": "completed",
        "clusters": await cluster_task,
    }


def sub_router() -> APIRouter:
    """Create a bare sub-router (no prefix — the parent owns /api/v1/rugmaps)."""
    return APIRouter(tags=["rugmaps"])
