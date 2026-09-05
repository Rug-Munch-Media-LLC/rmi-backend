"""Cluster / holder analysis routes: /cluster-analysis, /anomaly-detection,
/ai-health, /auto-label, /auto-label/stats."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel

from app.core.errors import AppError
from app.core.logging import get_logger
from app.routers.rugmaps_router._helpers import _get_rm, sub_router

logger = get_logger(__name__)

router = sub_router()


class HolderClusterRequest(BaseModel):
    token_address: str
    chain: str = "solana"
    holders: list[dict[str, Any]] | None = None


@router.post("/cluster-analysis")
async def ai_holder_cluster_analysis(req: HolderClusterRequest) -> dict[str, Any]:
    """AI-powered wallet cluster analysis for suspicious patterns.

    Uses the remote long (deep-reasoning) tier first, then a DeepSeek fallback
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

    Uses the remote long (deep-reasoning) tier first, then a DeepSeek fallback
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
    """Check RugMaps AI analysis availability (remote-tier routing)."""
    import httpx

    # NOTE: app.rugmaps_ai references these via os.getenv at call time but does
    # not export module-level constants; read them here so the health check
    # does not raise ImportError. Defaults mirror app/ai_analysis.py.
    litellm_base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
    litellm_api_key = os.getenv("LITELLM_API_KEY", "")
    ollama_base_urls = [u for u in os.getenv("OLLAMA_BASE_URLS", "http://localhost:11434").split(",") if u]

    ollama_ok = False
    ollama_url = ""
    for base_url in ollama_base_urls:
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
                litellm_base_url.replace("/v1", "/health") + "/readiness",
                headers={"Authorization": f"Bearer {litellm_api_key}"},
            )
            litellm_ok = resp.status_code in (200, 202)
    except Exception as e:
        logger.debug(f"LiteLLM health probe failed: {e}")

    return {
        "status": "ok" if (ollama_ok or litellm_ok) else "degraded",
        "service": "rugmaps-ai",
        "ollama_available": ollama_ok,
        "ollama_url": ollama_url,
        "litellm_available": litellm_ok,
        "litellm_url": litellm_base_url,
        "model": "remote-long -> deepseek",
        "fallback": "deepseek-direct + rule-based",
    }


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
