"""
Wallet Clustering API Router - Cluster detection, funding paths, risk analysis.
Connects to /api/v1/wallet-clusters/*
"""

from datetime import UTC

from fastapi import APIRouter

from app.core.errors import (
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from pydantic import BaseModel

from app.core.logging import get_logger

router = APIRouter(prefix="/api/v1/wallet-clusters", tags=["wallet-clustering"])
logger = get_logger(__name__)


class ClusterRequest(BaseModel):
    wallets: list[str]
    min_confidence: float = 0.3
    include_sleepers: bool = True


class FundingPathRequest(BaseModel):
    source: str
    target: str
    max_depth: int = 5


class BubblemapRequest(BaseModel):
    center_wallet: str
    depth: int = 2
    max_wallets: int = 250
    ai_deep_dive: bool = False


class ContractScanRequest(BaseModel):
    contract_address: str
    chain: str = "solana"
    detect_clusters: bool = True
    detect_bundles: bool = True


def _get_detector():
    try:
        from app.cluster_detection import ClusterDetectionPro

        return ClusterDetectionPro()
    except ImportError as e:
        raise ServiceUnavailableError(message=f"Module unavailable: {e}") from e


def _get_engine():
    try:
        from app.wallet_clustering import get_clustering_engine

        return get_clustering_engine()
    except ImportError as e:
        raise ServiceUnavailableError(message=f"Module unavailable: {e}") from e


@router.post("/detect")
async def detect_clusters(req: ClusterRequest):
    """Detect wallet clusters from a list of addresses (7 methods)."""
    if len(req.wallets) < 2:
        raise ValidationError(message="Need at least 2 wallets")
    if len(req.wallets) > 100:
        raise ValidationError(message="Max 100 wallets per request")

    detector = _get_detector()
    clusters = await detector.detect_clusters(
        wallets=req.wallets,
        min_confidence=req.min_confidence,
        include_sleepers=req.include_sleepers,
    )
    return {
        "total_wallets": len(req.wallets),
        "clusters_found": len(clusters),
        "clusters": [c.to_dict() for c in clusters],
    }


@router.post("/funding-path")
async def trace_funding_path(req: FundingPathRequest):
    """Trace the funding path between two wallets (BFS)."""
    detector = _get_detector()
    path = await detector.trace_funding_path(
        source=req.source, target=req.target, max_depth=req.max_depth
    )
    if path:
        return {"source": req.source, "target": req.target, "path": path.to_dict()}
    return {"source": req.source, "target": req.target, "path": None, "note": "No path found"}


@router.post("/scan")
async def scan_all():
    """Run all 4 clustering methods and return merged results."""
    engine = _get_engine()
    clusters = engine.find_all_clusters()
    return {"clusters": [c.to_dict() for c in clusters], "total_clusters": len(clusters)}


@router.get("/health")
async def clustering_health():
    cache_stats = {}
    gnn_status = {}
    spam_stats = {}
    try:
        from app.chain_cache import get_chain_cache

        cache_stats = await get_chain_cache().stats()
    except Exception:
        pass
    try:
        from app.fraud_gnn import get_fraud_gnn

        gnn_status = get_fraud_gnn().status()
    except Exception:
        gnn_status = {"error": "unavailable"}
    try:
        from app.spam_registry import get_spam_registry

        spam_stats = get_spam_registry().stats()
    except Exception:
        spam_stats = {"error": "unavailable"}
    return {
        "status": "ok",
        "service": "wallet-clustering-engine",
        "cache": cache_stats,
        "gnn": gnn_status,
        "spam_registry": spam_stats,
    }


@router.get("/report/{cluster_id}")
async def get_cluster_report(cluster_id: str):
    """Get detailed report for a specific cluster."""
    engine = _get_engine()
    report = engine.get_cluster_report(cluster_id)
    if not report:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return report


@router.post("/bubble-map-data")
async def get_bubble_map_data(req: BubblemapRequest):
    """Generate bubble map data for a wallet.
    Supports up to 250 wallets deep. Optional AI deep dive for advanced forensics."""
    engine = _get_engine()
    depth = min(
        req.depth, 10
    )  # Cap depth at 10 to prevent infinite loops, but max_wallets handles the 250 limit
    data = engine.generate_bubble_map_data(
        center_wallet=req.center_wallet, depth=depth, max_wallets=min(req.max_wallets, 250)
    )

    # AI Deep Dive: If requested and cluster is large/suspicious, trigger AI analysis
    if req.ai_deep_dive and data.get("nodes") and len(data["nodes"]) >= 5:
        try:
            from app.ai_router import router as ai_router

            # Extract top suspicious wallets for AI context
            suspicious_wallets = [
                n
                for n in data["nodes"]
                if n.get("type") in ["scammer", "exchange"] or n.get("volume", 0) > 10000
            ][:10]

            prompt = f"""Analyze this wallet cluster for {req.center_wallet}.
            Center wallet: {req.center_wallet}
            Total connected wallets: {len(data["nodes"])}
            Suspicious nodes: {suspicious_wallets}

            Provide a 3-bullet point forensic breakdown:
            1. Primary risk vector (honeypot, bundle, wash trading, etc.)
            2. Key suspicious wallets and their roles
            3. Recommended action for the user

            Be concise, professional, and direct."""

            messages = [
                {
                    "role": "system",
                    "content": "You are an elite blockchain forensics analyst. Provide concise, actionable intelligence.",
                },
                {"role": "user", "content": prompt},
            ]

            result = await ai_router.chat_completion(  # type: ignore
                messages=messages, tier="T2", temperature=0.2, max_tokens=400, timeout=15.0
            )

            if "error" in result:
                data["ai_deep_dive_analysis"] = f"AI analysis failed: {result['error']}"
            else:
                data["ai_deep_dive_analysis"] = result.get(
                    "content", "AI analysis completed but returned empty."
                )
        except Exception as e:
            logger.warning(f"AI deep dive failed: {e}")
            data["ai_deep_dive_analysis"] = "AI analysis temporarily unavailable."

    return data


@router.post("/ai-forensic-breakdown")
async def get_ai_forensic_breakdown(req: BubblemapRequest):
    """
    Premium AI-driven forensic breakdown that dynamically pulls deeper if necessary.

    This is a unique feature that analyzes the initial cluster (up to 250 wallets) for
    risk vectors. If complex layering, high-risk patterns, or obfuscation tactics are
    detected, it automatically expands the search depth (up to 1000 wallets) to provide
    a comprehensive forensic breakdown that competitors lack.
    """
    engine = _get_engine()

    # Use the new AI forensic breakdown method
    breakdown = engine.generate_ai_forensic_breakdown(
        center_wallet=req.center_wallet,
        initial_depth=req.depth,
        initial_max_wallets=min(req.max_wallets, 250),
        max_expansion_depth=5,
        absolute_max_wallets=1000,
    )

    # If AI deep dive is explicitly requested, enrich with LLM analysis
    if req.ai_deep_dive and breakdown.get("total_wallets_analyzed", 0) > 0:
        try:
            from app.ai_router import router as ai_router

            prompt = f"""You are an elite blockchain forensics analyst. Analyze this wallet cluster.

CENTER WALLET: {req.center_wallet}
ANALYSIS MODE: {breakdown.get("analysis_mode")}
RISK SCORE: {breakdown.get("risk_score")}/1.0
RISK VECTORS: {", ".join(breakdown.get("risk_vectors", ["None"]))}
TOTAL WALLETS ANALYZED: {breakdown.get("total_wallets_analyzed")}
TOTAL CONNECTIONS: {breakdown.get("total_connections_analyzed")}

WALLET PROFILES (top 10 by volume):
{chr(10).join([f"- {w['address']}: {w['total_volume']} vol, {w['total_transactions']} txs" for w in breakdown.get("wallet_profiles", [])[:10]])}

Provide a concise, professional forensic breakdown:
1. Primary risk vector and obfuscation tactics used
2. Key suspicious wallets and their likely roles (funder, mule, cash-out)
3. Clear, actionable recommendation for the user

Be direct and avoid fluff."""

            messages = [
                {
                    "role": "system",
                    "content": "You are an elite blockchain forensics analyst. Provide concise, actionable intelligence.",
                },
                {"role": "user", "content": prompt},
            ]

            result = await ai_router.chat_completion(  # type: ignore
                messages=messages, tier="T2", temperature=0.2, max_tokens=600, timeout=20.0
            )

            if "error" in result:
                breakdown["ai_llm_analysis"] = f"AI analysis failed: {result['error']}"
            else:
                breakdown["ai_llm_analysis"] = result.get(
                    "content", "AI analysis completed but returned empty."
                )
        except Exception as e:
            logger.warning(f"AI LLM deep dive failed: {e}")
            breakdown["ai_llm_analysis"] = (
                "AI LLM analysis temporarily unavailable, but heuristic forensic data is provided."
            )

    return breakdown


@router.post("/contract-scan")
async def scan_contract_clusters(req: ContractScanRequest):
    """Scan a contract for wallet clusters among its holders.
    Finds real holder wallets via Helius, then detects clusters."""
    engine = _get_engine()
    detector = _get_detector()

    # Step 1: Get real holders from chain (multi-source fallback)
    holders: list[str] = []
    try:
        from app.unified_provider import get_unified_provider

        provider = get_unified_provider()
        holder_data = await provider.get_token_holders(req.contract_address, limit=30)
        holders = [h.get("address") for h in holder_data if h.get("address")]

        # Filter out exchange/DeFi infrastructure
        excluded_entities = []
        try:
            from app.entity_registry import get_entity_registry

            registry = get_entity_registry()
            holders, excluded_entities = registry.filter_infrastructure(holders)
        except Exception as e:
            logger.debug(f"Entity filter skipped: {e}")

    except Exception as e:
        logger.warning(f"Provider failed: {e}")

    # Step 2: Feed transactions for holders into engine
    if holders and req.detect_clusters:
        try:
            from app.unified_provider import get_unified_provider

            provider = get_unified_provider()
            for h in holders[:5]:  # Limit API calls - feed top 5 holders
                txs = await provider.get_wallet_transactions(h, limit=10)
                from datetime import datetime

                from app.wallet_clustering import Transaction

                for t in txs:
                    ts = t.get("timestamp")
                    dt = datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(UTC)
                    engine.add_transaction(
                        Transaction(
                            signature=t.get("signature", ""),
                            timestamp=dt,
                            from_address=h,
                            to_address="unknown",
                            amount=0,
                            token="SOL",
                            program="system",
                        )
                    )
        except Exception as e:
            logger.warning(f"Feed failed: {e}")

    result = {
        "contract": req.contract_address,
        "chain": req.chain,
        "total_holders": len(holders) + len(excluded_entities),
        "holders": holders[:10],
        "excluded_infrastructure": [
            {"address": e.address, "entity": e.entity_name, "type": e.entity_type}
            for e in excluded_entities
        ],
        "holders_labeled": sum(1 for h in holders if engine._get_known_scammer_wallets()),
        "spam_check": {},
    }

    # Spam/sanctions check
    try:
        from app.spam_registry import get_spam_registry

        sr = get_spam_registry()
        result["spam_check"] = sr.check_token(req.contract_address, req.chain)
    except Exception as e:
        logger.debug(f"Spam check skipped: {e}")

    # Step 3: Detect clusters
    if req.detect_clusters and len(holders) >= 2:
        clusters = await detector.detect_clusters(
            wallets=holders[:50], min_confidence=0.3, include_sleepers=True
        )
        result["clusters_found"] = len(clusters)
        result["clusters"] = [c.to_dict() for c in clusters]

        # GNN fraud scoring on cluster wallets
        if clusters:
            try:
                from app.fraud_gnn import get_fraud_gnn

                gnn = get_fraud_gnn()
                all_wallets = [w for c in clusters for w in c.wallets]
                fps = [{"address": w, "tx_count": 1} for w in all_wallets[:20]]
                gnn_scores = gnn.score_wallets(fps)
                high_risk = [s for s in gnn_scores if s.get("fraud_probability", 0) >= 0.5]
                result["gnn_scoring"] = {
                    "wallets_scored": len(gnn_scores),
                    "high_risk_count": len(high_risk),
                    "avg_fraud_probability": round(
                        sum(s.get("fraud_probability", 0) for s in gnn_scores)
                        / max(len(gnn_scores), 1),
                        4,
                    ),
                    "model": gnn.status().get("model_type", "unknown"),
                }
            except Exception as e:
                logger.debug(f"GNN scoring skipped: {e}")

    # Step 4: Bundle detection
    if req.detect_bundles and holders:
        try:
            from app.bundle_detector import get_bundle_detector

            bd = get_bundle_detector()
            bundle = await bd.detect(
                token_address=req.contract_address,
                chain=req.chain,
                holders=holder_data,
                transactions=None,  # FEATURE GAP: Transaction data not yet wired to bundle detector. Bundle detection uses holder distribution & temporal patterns only.
            )
            result["bundle_detection"] = {
                "is_bundled": bundle.is_bundled,
                "confidence": bundle.confidence,
                "risk_label": bundle.risk_label,
                "signals": {
                    "atomic_block": bundle.atomic_block_score,
                    "common_funder": bundle.common_funder_score,
                    "temporal": bundle.temporal_score,
                    "distribution_anomaly": bundle.distribution_anomaly_score,
                    "concentration": bundle.concentration_score,
                },
                "details": {
                    "top10_pct": bundle.top10_holder_pct,
                    "top3_pct": bundle.top3_holder_pct,
                    "identical_amounts": bundle.identical_amount_count,
                    "round_amounts": bundle.round_amount_count,
                    "holder_count": bundle.holder_count,
                    "common_funder": bundle.common_funder_address,
                },
            }
        except Exception as e:
            logger.warning(f"Bundle detection failed: {e}")

    return result
