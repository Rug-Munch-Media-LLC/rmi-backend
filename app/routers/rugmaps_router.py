"""

RugMaps API Router - RMI's interactive wallet visualization.
Self-contained engine, no RugMaps - no external API dependency.
Connects to /api/v1/rugmaps/*
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.errors import AppError, ServiceUnavailableError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/rugmaps", tags=["rugmaps"])


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


@router.post("/map")
async def generate_rug_map(req: RugMapsRequest) -> dict[str, Any]:
    """Generate interactive RugMap for a wallet (2-hop default)."""
    rm = _get_rm()
    try:
        result = await rm.generate_map(
            center_wallet=req.center_wallet, depth=min(req.depth, 5), min_strength=req.min_strength
        )
        return result.to_dict()
    except ValueError as e:
        raise ValidationError(str(e)) from e
    except Exception as e:
        raise AppError(message=str(e)[:500]) from e


@router.get("/analyze/{address}")
async def analyze_wallet(address: str, depth: int = Query(2, ge=1, le=5)) -> dict[str, Any]:
    """Quick wallet analysis with RugMaps data."""
    rm = _get_rm()
    result = await rm.generate_map(center_wallet=address, depth=depth)
    d = result.to_dict()
    return {
        "address": address,
        "node_count": len(d.get("nodes", [])),
        "link_count": len(d.get("links", [])),
        "risk_level": d.get("risk_level", "unknown"),
        "risk_score": d.get("risk_score", 0),
        "stats": d.get("stats", {}),
        "top_counterparties": [n["address"][:12] + "..." for n in d.get("nodes", []) if n.get("layer") == 1][:10],
    }


@router.get("/entity/{address}")
async def get_entity_info(address: str) -> dict[str, Any]:
    """Get entity information for an address."""
    rm = _get_rm()
    info = await rm._get_entity_info(address)
    txs = await rm._get_transactions(address)
    risk, level = rm._calculate_risk(txs, node_count=1)
    return {"address": address, "entity": info, "risk_score": risk, "risk_level": level}


@router.get(
    "/health",
    summary="Rugmaps Health",
    description="Health check for the RugMaps visualization engine.",
)
async def rugmaps_health() -> dict[str, str]:
    return {"status": "ok", "service": "rugmaps-engine"}


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


def _format_currency(value: float) -> str:
    """Format a number as a compact currency string."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.2f}"


# ── Wallet labels + fund flows (OUR data enrichment) ─────────────


@router.get("/wallet-labels")
async def rugmaps_wallet_labels(
    addresses: str = Query(..., description="Comma-separated wallet addresses (max 200)"),
    chain: str = Query("solana"),
) -> dict[str, Any]:
    """Batch wallet labels for the RugMaps canvas.

    Resolves each address against OUR own data — wallet_v2 Qdrant collection,
    ClickHouse ``wallet_memory.wallet_labels``, and the DataBus wallet
    scanner — and returns a per-address label record with a node ``class``
    (CEX / DEX / mixer / bridge / whale / bot / sniper / dev / normal) that
    the frontend colors by.
    """
    addrs = [a.strip() for a in addresses.split(",") if a.strip()]
    if not addrs:
        raise ValidationError("Provide at least one address")
    if len(addrs) > 200:
        raise ValidationError("Max 200 addresses per request")

    from app.rugmaps_wallet import batch_lookup_wallet_labels

    return await batch_lookup_wallet_labels(addrs, chain=chain)


@router.get("/fund-flows")
async def rugmaps_fund_flows(
    token: str = Query(..., description="Token address the map is centered on"),
    address: str = Query(..., description="Wallet address to trace"),
    chain: str = Query("solana"),
    hops: int = Query(1, ge=1, le=2),
) -> dict[str, Any]:
    """Fund-flow arrows for a clicked wallet node.

    Returns inbound (funds received by the wallet) and outbound (funds sent)
    transfers from our ClickHouse wallet_flow engine, plus a cross-chain
    profile from the cross-chain aggregator.
    """
    if not address.strip():
        raise ValidationError("address is required")

    from app.rugmaps_wallet import lookup_fund_flows

    return await lookup_fund_flows(token=token, address=address, chain=chain, hops=hops)


@router.get("/token-graph")
async def token_holder_graph(
    token_address: str = Query(...),
    chain: str = Query("solana"),
    limit: int = Query(50, ge=5, le=200),
    skip_cache: bool = Query(False),
) -> Any | dict[str, Any]:
    """Generate a token holder graph with AI analysis and RAG caching.
    Uses local Ollama (Qwen) first, DeepSeek paid fallback, + our FAISS
    indexes + wallet labels + semantic cache.
    Falls back to DexScreener pair data when on-chain holder graph is empty."""
    import asyncio

    from app.rugmaps_ai import (
        generate_ai_analysis,
        generate_fallback_analysis,
        get_cached_analysis,
        get_token_holders,
        get_wallet_labels,
        search_similar_scams,
        set_cached_analysis,
    )

    # Check cache first (instant return for previously scanned tokens)
    if not skip_cache:
        cached = await get_cached_analysis(token_address, chain)
        if cached:
            logger.info(f"RugMaps cache HIT for {token_address[:12]}...")
            return {**cached, "from_cache": True}

    rm = _get_rm()

    # Get the RugMap concurrently with other data fetches.
    # Bound the native graph build (deep BFS with per-wallet chain fetches
    # can exceed gunicorn's 120s worker timeout) so we fail fast and fall
    # back to DexScreener instead of hanging the request.
    d: dict[str, Any] = {"nodes": [], "links": [], "stats": {}}
    map_failed = False
    try:
        result = await asyncio.wait_for(
            rm.generate_map(center_wallet=token_address, depth=3, min_strength=0.05),
            timeout=8,
        )
        d = result.to_dict()
    except Exception as e:
        map_failed = True
        logger.debug(f"Native graph generation failed for {token_address[:12]}, will try DexScreener: {e}")

    nodes = d.get("nodes", [])
    links = d.get("links", [])

    # ── CRITICAL FIX: When native graph returns empty (token contract, not a wallet),
    #     fall back to DexScreener pair data to build a meaningful visualization ──
    dex_data = None
    if len(nodes) <= 1 or not links or map_failed:
        dex_pair = await _fetch_dexscreener_token_info(token_address, chain)
        if dex_pair:
            logger.info(f"Building DexScreener graph for {token_address[:12]} (native graph empty)")
            dex_graph = _build_dexscreener_graph(dex_pair, token_address, chain)
            nodes = dex_graph.get("nodes", [])
            links = dex_graph.get("links", [])
            dex_data = dex_graph.get("dex_data")

    # Enrich nodes concurrently
    enriched_nodes = []
    addresses_for_labels = []
    for node in nodes[:limit]:
        addr = node.get("address") or node.get("id", "")
        addresses_for_labels.append(addr)
        enriched = dict(node)
        enriched["explorer_url"] = enriched.get("explorer_url") or _get_explorer_url(chain, addr)
        enriched_nodes.append(enriched)

    # Fetch wallet labels and similar scams in parallel.
    # Bound the whole gather: get_wallet_labels() fetches per-address chain
    # transactions (up to 50 x 15s) which alone can exceed the worker timeout.
    try:
        labels, similar_scams, holders = await asyncio.wait_for(
            asyncio.gather(
                get_wallet_labels(addresses_for_labels),
                search_similar_scams(token_address, chain),
                get_token_holders(token_address, chain),
                return_exceptions=True,
            ),
            timeout=15,
        )
    except TimeoutError:
        logger.warning(f"RugMaps enrichment gather timed out for {token_address[:12]}, using empty defaults")
        labels, similar_scams, holders = {}, [], []

    if isinstance(labels, Exception):
        labels = {}
    if isinstance(similar_scams, Exception):
        similar_scams = []
    if isinstance(holders, Exception):
        holders = []

    # Enrich nodes with labels
    for node in enriched_nodes:
        addr = node.get("address", "")
        label_data = labels.get(addr, {})
        entity = label_data.get("entity", {})
        node["label"] = entity.get("name") or entity.get("label") or node.get("label")
        node["entity_type"] = entity.get("type") or entity.get("entity_type")
        node["is_dev"] = entity.get("is_dev") or entity.get("type") == "developer"
        node["risk"] = label_data.get("risk_score") or node.get("risk_score", 0)
        node["tags"] = entity.get("tags") or []
        node["risk_level"] = label_data.get("risk_level", "unknown")

    # Generate AI analysis (Ollama-first + fallback to our data engine).
    graph_data = {
        "node_count": len(enriched_nodes),
        "link_count": min(len(links), limit * 2),
        "risk_score": d.get("risk_score", 0),
        "risk_level": d.get("risk_level", "unknown"),
        "stats": d.get("stats", {}),
    }

    # Generate AI analysis (Ollama-first + fallback to our data engine).
    # Bound the LLM call: generate_ai_analysis chains Ollama (30s), LiteLLM
    # (httpx timeout 120s), then a DeepSeek fallback (30s) — unbounded it can
    # hold the request for a long time and blow the frontend/proxy timeout
    # (HTTP 000 >25s). A 15s cap makes the endpoint fail fast to rule-based
    # (non-AI) graph data instead.
    try:
        ai_analysis = await asyncio.wait_for(
            generate_ai_analysis(token_address, chain, graph_data, holders, labels, similar_scams),
            timeout=15,
        )
    except TimeoutError:
        logger.warning(
            f"RugMaps AI analysis timed out after 15s for {token_address[:12]}, "
            "returning basic graph data (no AI annotation)"
        )
        ai_analysis = generate_fallback_analysis(
            d.get("risk_score", 0),
            holders,
            labels,
            similar_scams,
            len(enriched_nodes),
            min(len(links), limit * 2),
        )
    except Exception as e:
        logger.warning(f"AI analysis failed, using fallback: {e}")
        ai_analysis = generate_fallback_analysis(
            d.get("risk_score", 0),
            holders,
            labels,
            similar_scams,
            len(enriched_nodes),
            min(len(links), limit * 2),
        )

    response = {
        "token_address": token_address,
        "chain": chain,
        "nodes": enriched_nodes,
        "links": links[: limit * 2],
        **graph_data,
        "analysis": ai_analysis,
        "similar_scams": similar_scams[:5],
        "wallet_labels_count": len(labels),
        "from_cache": False,
    }
    if dex_data:
        response["dex_data"] = dex_data
        response["source"] = "dexscreener_fallback"

    # Cache for future requests
    set_cached_analysis(token_address, chain, response)

    # Auto-label all wallets in the graph (background, non-blocking)
    try:
        from app.auto_labeler import get_auto_labeler

        labeler = get_auto_labeler()
        for node in enriched_nodes:
            await labeler.observe_wallet(
                node["address"],
                chain,
                {"event": "scanned_in_graph", "token": token_address, "risk": node.get("risk", 0)},
            )
    except Exception as e:
        logger.debug(f"Auto-labeling failed for {token_address}: {e}")

    return response


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


# ── Auto-Labeler ──────────────────────────────────────────────


@router.post("/auto-label")
async def auto_label_wallet(request: dict[str, Any]) -> dict[str, str]:
    """Submit wallet observations for automatic labeling."""
    try:
        from app.auto_labeler import get_auto_labeler

        labeler = get_auto_labeler()

        address = request.get("address", "")
        chain = request.get("chain", "ethereum")
        events = request.get("events", [])

        all_labels = []
        for event in events:
            labels = await labeler.observe_wallet(address, chain, event)
            all_labels.extend(labels)

        return {
            "status": "ok",
            "address": address,
            "labels_applied": all_labels,
            "count": len(all_labels),
        }
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.get("/auto-label/stats")
async def auto_label_stats() -> dict[str, Any]:
    """Get auto-labeler statistics.

    Values are intentionally heterogeneous (ints, dicts, None) — the
    annotation is ``dict[str, Any]`` so FastAPI does not coerce/validate
    them as strings (previously caused ResponseValidationError 500).
    """
    try:
        from app.auto_labeler import get_auto_labeler

        labeler = get_auto_labeler()
        return {"status": "ok", **labeler.get_stats()}
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


# ── AI-Powered Analysis Endpoints (wired to LiteLLM qwen-analysis) ──


class HolderClusterRequest(BaseModel):
    token_address: str
    chain: str = "solana"
    holders: list[dict[str, Any]] | None = None


@router.post("/cluster-analysis")
async def ai_holder_cluster_analysis(req: HolderClusterRequest) -> dict[str, Any]:
    """AI-powered wallet cluster analysis for suspicious patterns.

    Uses local Ollama (DeepSeek-Qwen-8B) first, then LiteLLM → qwen-analysis
    to identify bundled launches, sniper coordination, wash trading rings,
    insider clusters, and dev-linked wallets.
    """

    holders = req.holders or []

    # Fetch holders from graph engine if not provided
    if not holders:
        try:
            rm = _get_rm()
            result = await rm.generate_map(center_wallet=req.token_address, depth=2, min_strength=0.1)
            d = result.to_dict()
            holders = d.get("nodes", [])
        except Exception as e:
            logger.debug(f"Holder fetch for cluster analysis failed: {e}")

    # Get labels for holders
    addresses = [h.get("address") or h.get("id", "") for h in holders[:30]]
    from app.rugmaps_ai import analyze_holder_clusters, get_wallet_labels as _labels

    labels = await _labels(addresses)

    result = await analyze_holder_clusters(req.token_address, req.chain, holders, labels)
    return {
        "token_address": req.token_address,
        "chain": req.chain,
        "holder_count": len(holders),
        **result,
        "ai_model": result.get("ai_model", "rule-based"),
    }


class ContractAnomalyRequest(BaseModel):
    chain: str
    address: str
    holder_data: dict[str, Any] | None = None
    scanner_data: dict[str, Any] | None = None


@router.post("/anomaly-detection")
async def ai_contract_anomaly_detection(req: ContractAnomalyRequest) -> dict[str, Any]:
    """AI-powered contract anomaly detection for rug pull indicators.

    Uses local Ollama (DeepSeek-Qwen-8B) first, then LiteLLM → qwen-analysis
    to review token holder distribution, contract behavior, and on-chain
    patterns for honeypots, rug pulls, and manipulation indicators.
    """
    from app.rugmaps_ai import detect_contract_anomalies

    holder_data = req.holder_data or {}
    scanner_data = req.scanner_data or {}

    result = await detect_contract_anomalies(req.address, req.chain, holder_data, scanner_data)
    return {
        "token_address": req.address,
        "chain": req.chain,
        **result,
        "ai_model": result.get("ai_model", "rule-based"),
    }


@router.get("/ai-health")
async def ai_health_check() -> dict[str, Any]:
    """Check RugMaps AI analysis availability (Ollama-first routing)."""
    import os

    import httpx

    # NOTE: app.rugmaps_ai references these via os.getenv at call time but does
    # not export module-level constants; read them here so the health check
    # does not raise ImportError. Defaults mirror app/ai_analysis.py.
    from app.rugmaps_ai import (
        OLLAMA_COMPLEX_MODEL,
        OLLAMA_FAST_MODEL,
    )

    LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
    LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
    OLLAMA_BASE_URLS = [u for u in os.getenv("OLLAMA_BASE_URLS", "http://172.22.0.1:11434").split(",") if u]

    ollama_ok = False
    ollama_url = ""
    for base_url in OLLAMA_BASE_URLS:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code == 200:
                    ollama_ok = True
                    ollama_url = base_url
                    break
        except Exception as e:
            logger.debug(f"Ollama health probe failed on {base_url}: {e}")

    litellm_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                LITELLM_BASE_URL.replace("/v1", "/health") + "/readiness",
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
            litellm_ok = resp.status_code in (200, 202)
    except Exception as e:
        logger.debug(f"LiteLLM health probe failed: {e}")

    return {
        "status": "ok" if (ollama_ok or litellm_ok) else "degraded",
        "service": "rugmaps-ai",
        "ollama_available": ollama_ok,
        "ollama_url": ollama_url,
        "ollama_fast_model": OLLAMA_FAST_MODEL,
        "ollama_complex_model": OLLAMA_COMPLEX_MODEL,
        "litellm_available": litellm_ok,
        "litellm_url": LITELLM_BASE_URL,
        "model": "ollama-first -> litellm/qwen-analysis",
        "fallback": "deepseek-direct + rule-based",
    }
