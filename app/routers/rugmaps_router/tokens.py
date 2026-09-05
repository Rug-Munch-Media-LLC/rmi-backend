"""Token map data routes: /map, /analyze, /entity, /health, /token-graph,
/wallet-labels, /scan, /rug-warning, /deployer-tokens."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Query

from app.core.errors import AppError, ValidationError
from app.core.logging import get_logger
from app.routers.rugmaps_router._helpers import (
    RugMapsRequest,
    _build_dexscreener_graph,
    _fetch_dexscreener_token_info,
    _get_explorer_url,
    _get_rm,
    _run_token_risk_scan,
    sub_router,
)

logger = get_logger(__name__)

router = sub_router()


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
async def get_entity_info(address: str, chain: str = Query("solana")) -> dict[str, Any]:
    """Entity resolution for an address (wallet-cluster flow).

    Scanner-engine entity info first; when the engine has none (common for
    wallets outside its tx-graph cache), fall back to OUR wallet-label stack
    (wallet_v2 Qdrant + ClickHouse ``wallet_memory.wallet_labels`` + DataBus
    wallet scanner) so the map never renders an unexplained node when we
    hold label data the engine lacks. ``entity_source`` reports which path
    resolved; ``entity`` stays null when neither has data — never fabricated.
    """
    rm = _get_rm()
    info = await rm._get_entity_info(address)
    txs = await rm._get_transactions(address)
    risk, level = rm._calculate_risk(txs, node_count=1)

    entity = info
    entity_source = "scanner_engine"
    if not entity:
        try:
            from app.rugmaps_wallet import batch_lookup_wallet_labels

            labeled = await batch_lookup_wallet_labels([address], chain=chain)
            rec = (labeled.get("labels") or {}).get(address.strip().lower()) or {}
            if rec.get("label") or rec.get("entity_type") or rec.get("labels"):
                entity = {
                    "name": rec.get("label") or None,
                    "type": rec.get("entity_type") or rec.get("class") or None,
                    "tags": rec.get("labels") or [],
                    "risk_score": rec.get("risk_score"),
                    "class": rec.get("class"),
                }
                source = rec.get("source") or "wallet_labels"
                entity["source"] = source
                entity_source = source
        except Exception as e:
            logger.debug(f"wallet-label entity fallback failed for {address[:12]}: {e}")

    return {
        "address": address,
        "entity": entity,
        "entity_source": entity_source,
        "risk_score": risk,
        "risk_level": level,
    }


@router.get(
    "/health",
    summary="Rugmaps Health",
    description="Health check for the RugMaps visualization engine.",
)
async def rugmaps_health() -> dict[str, str]:
    return {"status": "ok", "service": "rugmaps-engine"}


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


@router.get("/token-graph")
async def token_holder_graph(
    token_address: str = Query(...),
    chain: str = Query("solana"),
    limit: int = Query(50, ge=5, le=200),
    skip_cache: bool = Query(False),
) -> Any | dict[str, Any]:
    """Generate a token holder graph with AI analysis and RAG caching.
    Uses the remote long (deep-reasoning) tier first, DeepSeek fallback, +
    our FAISS indexes + wallet labels + semantic cache.
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

    # Generate AI analysis (LLM-first + fallback to our data engine).
    graph_data = {
        "node_count": len(enriched_nodes),
        "link_count": min(len(links), limit * 2),
        "risk_score": d.get("risk_score", 0),
        "risk_level": d.get("risk_level", "unknown"),
        "stats": d.get("stats", {}),
    }

    # Generate AI analysis (LLM-first + fallback to our data engine).
    # Bound the LLM call: generate_ai_analysis chains LiteLLM (httpx timeout
    # 120s), then a DeepSeek fallback (30s) — unbounded it can hold the
    # request for a long time and blow the frontend/proxy timeout
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


@router.get("/scan")
async def rugmaps_scan_token(
    token: str = Query(..., min_length=2),
    chain: str = Query("solana"),
) -> dict[str, Any]:
    """Token risk scan for the rugcharts/rugmaps panels (ScamRiskMeter et al).

    Returns structured red_flags (category/severity/title) — never a bare
    string list — so the UI can render explainable risk factors.
    """
    return await _run_token_risk_scan(token.strip(), chain)


@router.get("/rug-warning")
async def rugmaps_rug_warning(
    chain: str = Query("solana"),
    token: str = Query(..., min_length=2),
) -> dict[str, Any]:
    """Active rug-pull warnings for a token (RugPullWarning panel).

    Derives warnings from the SENTINEL scan red flags; each warning carries
    level/category/confidence. Empty warnings ≠ safe — ``status`` reports
    whether the scan actually ran.
    """
    scan = await _run_token_risk_scan(token.strip(), chain)
    level_map = {"critical": "CRITICAL", "high": "WARNING", "medium": "CAUTION", "low": "CAUTION"}
    warnings: list[dict[str, Any]] = []
    for i, flag in enumerate(scan.get("red_flags", [])):
        warnings.append(
            {
                "id": flag.get("id") or str(i),
                "level": level_map.get(str(flag.get("severity")), "CAUTION"),
                "category": flag.get("category") or "trading",
                "title": flag.get("title") or "",
                "description": flag.get("description") or "",
                "confidence": scan.get("confidence", 0) or 50,
                "detected_at": int(datetime.now(UTC).timestamp()),
                "recommended_action": "Verify liquidity lock and holder distribution before trading."
                if flag.get("severity") in ("high", "critical")
                else "Exercise caution; monitor for further risk signals.",
            }
        )
    return {
        "token": token,
        "chain": chain,
        "risk_score": scan.get("risk_score"),
        "risk_level": scan.get("risk_level"),
        "warnings": warnings,
        "status": scan.get("status"),
    }


@router.get("/deployer-tokens")
async def rugmaps_deployer_tokens(
    deployer: str = Query(..., min_length=8),
    days: int = Query(30, ge=1, le=365),
    chain: str | None = Query(None),
) -> dict[str, Any]:
    """Tokens previously launched by a deployer wallet (DeployerNetwork panel).

    Backed by wallet_memory deployer history (ClickHouse). Empty list means
    no recorded launches — never fabricated.
    """
    try:
        from app.wallet_memory.storage import get_storage

        storage = get_storage()
        history = await storage.get_deployer_history(deployer)
    except Exception as e:
        logger.debug(f"deployer history failed for {deployer[:12]}: {e}")
        return {"deployer": deployer, "chain": chain, "days": days, "tokens": [], "source": "unavailable"}

    cutoff = datetime.now(UTC).timestamp() - days * 86400
    tokens: list[dict[str, Any]] = []
    for entry in history if isinstance(history, list) else []:
        if not isinstance(entry, dict):
            continue
        launched = entry.get("launched_at") or entry.get("created_at") or entry.get("timestamp") or 0
        try:
            launched_ts = float(launched)
        except (TypeError, ValueError):
            continue
        if launched_ts and launched_ts < cutoff:
            continue
        t_chain = entry.get("chain") or chain
        if chain and t_chain != chain:
            continue
        tokens.append(
            {
                "chain": t_chain,
                "address": entry.get("address") or entry.get("token_address") or entry.get("mint") or "",
                "symbol": entry.get("symbol") or entry.get("token_symbol") or "?",
                "name": entry.get("name"),
                "launched_at": launched_ts,
                "peak_mcap": float(entry.get("peak_mcap") or entry.get("peak_market_cap") or 0),
                "current_mcap": float(entry.get("current_mcap") or entry.get("market_cap") or 0),
            }
        )
    return {
        "deployer": deployer,
        "chain": chain,
        "days": days,
        "tokens": tokens,
        "count": len(tokens),
        "source": "wallet_memory",
    }
