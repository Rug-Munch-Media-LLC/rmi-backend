"""
Security Intelligence Router v2 - Complete Crypto Security Stack
===================================================================

All crypto security modules exposed via REST:
  • Contract: bytecode scan + deep scan (Slither + Mythril)
  • Wallet: reputation + threat intel + anomaly detection
  • Graph: clustering + fund flow + cross-chain correlation
  • Mempool: real-time attack detection + sentinel control
  • Threat: OFAC + local blocklist + batch lookup
  • Dashboard: alert aggregation + monitoring + scoring
  • ML: anomaly detection on wallets + token metrics
  • CrossChain: wallet linking across chains

Updated 2026-05-08 for world-class crypto intelligence.
"""

from __future__ import annotations

# ═══ LAZY IMPORTS (deferred to first use - saves ~2.2s cold start) ═══
import importlib as _il
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.mempool_sentinel import SentinelManager
    from app.ml_anomaly import TokenMetricAnomalyDetector

_lazy_cache: dict[str, object] = {}


def _L(module_path) -> Any:
    if module_path not in _lazy_cache:
        _lazy_cache[module_path] = _il.import_module(module_path)
    return _lazy_cache[module_path]


# Lightweight imports (eager - these are fast)
from fastapi import APIRouter  # noqa: E402 — module docstring precedes import block
from pydantic import BaseModel, Field  # noqa: E402 — module docstring precedes import block

from app.cross_chain_correlator import (  # noqa: E402 — module docstring precedes import block
    ChainFingerprint,
    CrossChainCorrelator,
)

router = APIRouter(prefix="/api/v1/security", tags=["security"])

# ─── SINGLETONS ────────────────────────────────────────────

_sentinel_manager = None
_wallet_detector = None
_token_detector = None


async def get_sentinel() -> SentinelManager:
    global _sentinel_manager
    if _sentinel_manager is None:
        _sentinel_manager = _L("app.mempool_sentinel").SentinelManager()
    return _sentinel_manager


async def get_wallet_detector() -> Any:
    global _wallet_detector
    if _wallet_detector is None:
        _ml = _L("app.ml_anomaly")
        _wallet_detector = _ml.WalletAnomalyDetector(contamination=0.05)
    return _wallet_detector


async def get_token_detector() -> TokenMetricAnomalyDetector:
    global _token_detector
    if _token_detector is None:
        _ml = _L("app.ml_anomaly")
        _token_detector = _ml.TokenMetricAnomalyDetector()
    return _token_detector


# ─── MODELS ────────────────────────────────────────────────


class ContractScanRequest(BaseModel):
    address: str = Field(..., min_length=32, max_length=44)
    chain: str = Field(default="ethereum")
    deep: bool = Field(default=False, description="Run Slither + Mythril deep scan")


class BatchContractRequest(BaseModel):
    addresses: list[str] = Field(..., min_length=1, max_length=50)
    chain: str = Field(default="ethereum")
    deep: bool = Field(default=False)


class WalletCheckRequest(BaseModel):
    address: str = Field(..., min_length=32, max_length=44)
    chain: str = Field(default="auto")


class WalletAnalyzeRequest(BaseModel):
    address: str = Field(..., min_length=32, max_length=44)
    transactions: list[dict[str, Any]] = Field(default_factory=list, description="Raw tx list for ML analysis")
    chain: str = Field(default="auto")


class GraphAnalyzeRequest(BaseModel):
    transactions: list[dict[str, Any]] = Field(..., min_length=2, max_length=1000)
    chain: str = Field(default="ethereum")
    analysis_types: list[str] = Field(default=["clusters", "flows", "anomalies"])


class ThreatBatchRequest(BaseModel):
    addresses: list[str] = Field(..., min_length=1, max_length=100)
    chain: str = Field(default="evm")


class CrossChainRequest(BaseModel):
    fingerprints: list[dict[str, Any]] = Field(..., min_length=2, description="Chain fingerprints")
    chains: list[str] = Field(default=["ethereum", "base", "solana"])


class TokenMetricRequest(BaseModel):
    token: str = Field(...)
    metrics: dict[str, float] = Field(..., description="Current metric values")


class AlertAckRequest(BaseModel):
    alert_id: str = Field(...)


# ─── CONTRACT ANALYSIS ─────────────────────────────────────


@router.get(
    "/contract/{address}",
    summary="Get Contract Intelligence",
    description="Get contract security intelligence including Mythril and Slither analysis results, known vulnerabilities, and risk scoring.",
)
async def contract_analysis(address: str, chain: str = "ethereum", deep: bool = False) -> Any:
    """Analyze contract - bytecode scan + optional Slither/Mythril deep scan."""

    # Fast bytecode scan
    _oca = _L("app.onchain_analyzer")
    result = await _oca.analyze_contract(address, chain)
    response = {
        "address": result.address,
        "chain": result.chain,
        "is_contract": result.is_contract,
        "is_proxy": result.is_proxy,
        "proxy_type": result.proxy_type,
        "proxy_implementation": result.proxy_implementation,
        "risk_score": result.risk_score,
        "risk_flags": result.risk_flags,
        "detected_interfaces": result.detected_interfaces,
        "has_selfdestruct": result.has_selfdestruct,
        "has_delegatecall": result.has_delegatecall,
        "has_mint": result.has_mint,
        "has_burn": result.has_burn,
        "has_renounce_ownership": result.has_renounce_ownership,
        "has_reentrancy_guard": result.has_reentrancy_guard,
        "uses_tx_origin": result.uses_tx_origin,
        "uses_timestamp": result.uses_timestamp,
        "honeypot_indicators": result.honeypot_indicators,
        "anti_bot_detected": result.anti_bot_detected,
        "uniswap_pair": result.uniswap_pair,
        "analysis_time": result.analysis_time,
    }

    # Deep scan (if requested)
    if deep:
        try:
            _deep = _L("app.contract_deepscan")
            deep_result = await _deep.deep_scan_contract(address, chain, use_cache=True)
            response["deep_scan"] = deep_result.to_dict()
            # Merge scores
            response["risk_score"] = max(response["risk_score"], deep_result.risk_score)
            response["deep_vulnerabilities"] = len(deep_result.vulnerabilities)
        except Exception as e:
            response["deep_scan_error"] = str(e)

    return response


@router.post("/scan/batch")
async def batch_contract_scan(req: BatchContractRequest) -> dict[str, Any]:
    """Batch contract scan - fast or deep."""
    if req.deep:
        _deep = _L("app.contract_deepscan")
        results = await _deep.batch_deep_scan(req.addresses, req.chain)
        return {
            "scanned": len(results),
            "chain": req.chain,
            "high_risk": [r.address for r in results if r.risk_score >= 70],
            "critical": [r.address for r in results if r.risk_score >= 90],
            "results": [r.to_dict() for r in results],
        }
    else:
        _oca = _L("app.onchain_analyzer")
        results = await _oca.batch_analyze_contracts(req.addresses, req.chain)
        return {
            "scanned": len(results),
            "chain": req.chain,
            "high_risk": [r.address for r in results if r.risk_score >= 70],
            "proxies": [r.address for r in results if r.is_proxy],
            "honeypots": [r.address for r in results if r.honeypot_indicators],
            "results": [
                {
                    "address": r.address,
                    "risk_score": r.risk_score,
                    "risk_flags": r.risk_flags,
                    "is_proxy": r.is_proxy,
                    "honeypot": r.honeypot_indicators,
                }
                for r in results
            ],
        }


# ─── WALLET / THREAT INTEL ──────────────────────────────────


@router.get("/wallet/{address}")
async def wallet_security_check(address: str, chain: str = "auto") -> dict[str, Any]:
    """Full wallet security check: reputation + threat intel."""
    _cg = _L("app.crypto_guard")
    reputation = await _cg.check_wallet_reputation(address, chain)
    _ti = _L("app.threat_intel")
    agg = await _ti.get_aggregator()
    threat_rep = await agg.check_address(address, chain)

    final_score = max(reputation.get("score", 0), threat_rep.risk_score)

    return {
        "address": address,
        "chain": chain,
        "risk_score": final_score,
        "blocked": reputation.get("blocked", False) or threat_rep.is_blocked,
        "sanctioned": threat_rep.is_sanctioned,
        "scam": threat_rep.is_scam,
        "mixer": threat_rep.is_mixer,
        "categories": list(threat_rep.categories),
        "tags": list(threat_rep.tags),
        "threat_reports": len(threat_rep.threat_reports),
        "reputation_flags": reputation.get("flags", []),
        "first_flagged": threat_rep.first_flagged,
        "last_flagged": threat_rep.last_flagged,
    }


@router.post("/wallet/analyze")
async def wallet_ml_analysis(req: WalletAnalyzeRequest) -> dict[str, Any]:
    """ML behavioral analysis of wallet transactions."""
    # TODO(security-intel): endpoints in this module call APIs that do not match
    # the current class surfaces (ml_anomaly, CrossChainCorrelator,
    # TokenMetricAnomalyDetector, SentinelManager). They would raise
    # AttributeError at runtime; rework against the actual APIs.
    from app.ml_anomaly import extract_wallet_features  # type: ignore[attr-defined]

    features = extract_wallet_features(req.transactions, req.address)
    detector = await get_wallet_detector()

    # Fit detector if we have comparison data
    if req.transactions and len(req.transactions) > 5:
        detector.fit([features])
        result = detector.predict(features)
    else:
        result = {"error": "Insufficient transactions for ML analysis (need >5)"}

    return {
        "address": req.address,
        "features": {
            "tx_count": features.tx_count,
            "avg_tx_value": features.avg_tx_value,
            "unique_counterparties": features.unique_counterparties,
            "dex_swaps": features.dex_swaps,
            "burst_ratio": features.burst_ratio,
            "night_ratio": features.night_tx_ratio,
        },
        "ml_result": result,
    }


@router.post("/threat/check")
async def batch_threat_check(req: ThreatBatchRequest) -> dict[str, Any]:
    """Batch threat intel lookup."""
    _ti = _L("app.threat_intel")
    agg = await _ti.get_aggregator()
    results = await agg.batch_check(req.addresses, req.chain)

    return {
        "checked": len(results),
        "blocked": [a for a, r in results.items() if r.is_blocked],
        "sanctioned": [a for a, r in results.items() if r.is_sanctioned],
        "scams": [a for a, r in results.items() if r.is_scam],
        "mixers": [a for a, r in results.items() if r.is_mixer],
        "results": {a: r.to_dict() for a, r in results.items()},
    }


# ─── GRAPH ANALYSIS ─────────────────────────────────────────


@router.post("/graph/analyze")
async def graph_analyze(req: GraphAnalyzeRequest) -> Any:
    """Transaction graph: clusters, flows, anomalies."""
    _tga = _L("app.tx_graph_analyzer")
    graph = await _tga.build_graph_from_transactions(req.transactions, req.chain)

    response = {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "wallets": list(graph.nodes.keys())[:50],
    }

    if "clusters" in req.analysis_types:
        clusters = graph.cluster_by_louvain()
        response["clusters"] = [
            {
                "id": c.id,
                "wallet_count": len(c.wallets),
                "total_volume": c.total_volume,
            }
            for c in clusters[:10]
        ]

    if "shared_funding" in req.analysis_types:
        shared = graph.cluster_by_shared_funding(min_common=2)
        response["shared_funding_clusters"] = [
            {
                "id": c.id,
                "wallet_count": len(c.wallets),
                "shared_funders": c.shared_funders[:5],
            }
            for c in shared[:10]
        ]

    if "flows" in req.analysis_types:
        cycles = graph.detect_circular_flows(max_cycle_len=4)
        response["circular_flows"] = len(cycles)
        response["wash_trading_suspected"] = len(cycles) > 2

    if "anomalies" in req.analysis_types:
        bursts = graph.detect_burst_transfers(min_count=15)
        sudden = graph.detect_sudden_wealth(multiplier=20.0)
        response["burst_activity"] = bursts[:5]
        response["sudden_wealth"] = sudden[:5]

    if "export" in req.analysis_types:
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".gexf")
        os.close(fd)  # mktemp is insecure/deprecated — mkstemp creates with 0600 perms
        graph.export_gexf(path)
        response["gexf_export_path"] = path

    return response


# ─── CROSS-CHAIN ────────────────────────────────────────────


@router.post("/crosschain/correlate")
async def crosschain_correlate(req: CrossChainRequest) -> dict[str, Any]:
    """Find cross-chain wallet correlations."""
    correlator = CrossChainCorrelator(confidence_threshold=0.6)  # type: ignore[call-arg]

    # Add fingerprints
    for fp_data in req.fingerprints:
        import numpy as np

        fp = ChainFingerprint(  # type: ignore[call-arg]
            address=fp_data["address"],
            chain=fp_data["chain"],
            feature_vector=np.array(fp_data.get("features", []), dtype=np.float64),
            first_active=fp_data.get("first_active"),
            last_active=fp_data.get("last_active"),
            total_txs=fp_data.get("total_txs", 0),
            total_volume=fp_data.get("total_volume", 0.0),
            known_tags=fp_data.get("tags", []),
        )
        correlator.add_fingerprint(fp)  # type: ignore[attr-defined]

    # Find links
    links = correlator.find_all_links(req.chains)  # type: ignore[attr-defined]

    return {
        "fingerprints_analyzed": len(req.fingerprints),
        "links_found": len(links),
        "high_confidence_links": [line.to_dict() for line in links if line.confidence >= 0.8],
        "all_links": [line.to_dict() for line in links[:20]],
    }


# ─── ML / ANOMALY ───────────────────────────────────────────


@router.post("/ml/token/anomaly")
async def token_metric_anomaly(req: TokenMetricRequest) -> Any:
    """Detect token metric anomalies vs baseline."""
    detector = await get_token_detector()
    detector.update_baseline(req.token, req.metrics)  # type: ignore[attr-defined]
    result = detector.detect_anomaly(req.token, req.metrics)  # type: ignore[attr-defined]
    return result


# ─── MEMPOOL ────────────────────────────────────────────────


@router.get("/mempool/status")
async def mempool_status() -> dict[str, Any]:
    """Mempool sentinel status + recent detections."""
    sentinel = await get_sentinel()
    stats = sentinel.get_stats()  # type: ignore[attr-defined]

    patterns = sentinel.sentinel.get_recent_patterns(20) if sentinel.sentinel else []  # type: ignore[attr-defined]

    return {
        **stats,
        "recent_patterns": patterns,
    }


@router.post(
    "/mempool/start",
    summary="Mempool Start",
    description="Start monitoring the mempool for suspicious transactions on a specified chain.",
)
async def mempool_start(chain: str = "ethereum") -> dict[str, str]:
    sentinel = await get_sentinel()
    await sentinel.start(chain)  # type: ignore[attr-defined]
    return {"status": "started", "chain": chain}


@router.post("/mempool/stop", summary="Mempool Stop", description="Stop monitoring the mempool for a specified chain.")
async def mempool_stop() -> dict[str, str]:
    sentinel = await get_sentinel()
    await sentinel.stop()  # type: ignore[attr-defined]
    return {"status": "stopped"}


# ─── DASHBOARD ──────────────────────────────────────────────


@router.get("/dashboard/stats")
async def dashboard_stats() -> Any:
    """Security dashboard statistics."""
    _sd = _L("app.security_dashboard")
    dash = _sd.get_dashboard()
    return dash.get_stats()


@router.get("/dashboard/alerts")
async def dashboard_alerts(
    severity: str | None = None,
    source: str | None = None,
    limit: int = 100,
    unacknowledged: bool = False,
) -> Any:
    """Query security alerts."""
    _sd = _L("app.security_dashboard")
    dash = _sd.get_dashboard()
    return dash.get_alerts(severity, source, limit, unacknowledged)


@router.post("/dashboard/acknowledge")
async def dashboard_acknowledge(req: AlertAckRequest) -> dict[str, Any]:
    """Acknowledge an alert."""
    _sd = _L("app.security_dashboard")
    dash = _sd.get_dashboard()
    success = await dash.acknowledge(req.alert_id)
    return {"acknowledged": success}


@router.get("/dashboard/score")
async def dashboard_score(hours: int = 24) -> dict[str, Any]:
    """Get rolling security score."""
    _sd = _L("app.security_dashboard")
    dash = _sd.get_dashboard()
    return {
        "security_score": dash.get_security_score(hours),
        "window_hours": hours,
        "max": 100,
    }


@router.get("/dashboard/timeline/{target}")
async def dashboard_timeline(target: str) -> dict[str, Any]:
    """Get incident timeline for a target."""
    _sd = _L("app.security_dashboard")
    dash = _sd.get_dashboard()
    return {"timeline": dash.get_incident_timeline(target)}


# ─── ENTITY LABELING ─────────────────────────────────────────


@router.get("/entity/label/{address}")
async def entity_label(address: str) -> dict[str, Any]:
    """Lookup entity label for an address."""
    _el = _L("app.entity_labeler")
    entity = await _el.label_address(address)
    if not entity:
        return {"address": address, "entity": None, "known": False}
    return {"address": address, "entity": entity.to_dict(), "known": True}


@router.post("/entity/batch")
async def entity_batch_label(addresses: list[str]) -> Any:
    """Batch entity label lookup."""
    _el = _L("app.entity_labeler")
    return await _el.batch_label_addresses(addresses)


@router.get("/entity/search")
async def entity_search(q: str = "", category: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Search entity database."""
    _el = _L("app.entity_labeler")
    db = await _el.get_entity_labeler()
    results = await db.search_entities(query=q, category=category, limit=limit)
    return {"results": [r.to_dict() for r in results], "count": len(results)}


@router.get("/entity/stats")
async def entity_stats() -> Any:
    """Entity database statistics."""
    _el = _L("app.entity_labeler")
    db = await _el.get_entity_labeler()
    return await db.get_stats()


@router.get("/entity/{entity_id}")
async def entity_detail(entity_id: str) -> Any:
    """Full entity graph with addresses and relationships."""
    _el = _L("app.entity_labeler")
    db = await _el.get_entity_labeler()
    return await db.get_entity_graph(entity_id)


# ─── PORTFOLIO TRACKER ──────────────────────────────────────


@router.get("/portfolio/{wallet}")
async def portfolio_single(wallet: str, chain: str = "ethereum") -> dict[str, Any]:
    """Get portfolio for a single wallet."""
    _pt = _L("app.portfolio_tracker")
    portfolio = await _pt.get_portfolio(wallet, chain)
    return portfolio.to_dict()


@router.post("/portfolio/multi")
async def portfolio_multi(wallets: list[dict[str, str]]) -> dict[str, Any]:
    """Aggregate portfolio across multiple wallets/chains."""
    _pt = _L("app.portfolio_tracker")
    result = await _pt.get_multi_wallet_portfolio(wallets)
    return result.to_dict()


@router.get("/portfolio/history/{wallet}")
async def portfolio_history(wallet: str, chain: str = "ethereum", hours: int = 24) -> dict[str, Any]:
    """Historical net worth chart data."""
    _pt = _L("app.portfolio_tracker")
    history = await _pt.get_portfolio_history(wallet, chain, hours)
    return {
        "wallet": wallet,
        "chain": chain,
        "hours": hours,
        "data_points": len(history),
        "history": history,
    }


# ─── WALLET MONITOR ─────────────────────────────────────────


@router.post("/monitor/watch")
async def monitor_watch(address: str, chain: str = "ethereum", tags: str = "") -> dict[str, Any]:
    """Add a wallet to the watchlist."""
    _wm = _L("app.domains.wallet.wallet_monitor")
    manager = _wm.WalletMonitorManager()
    await manager.add_watch(address, chain, tags.split(",") if tags else [])
    return {"watched": True, "address": address, "chain": chain}


@router.post("/monitor/unwatch")
async def monitor_unwatch(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Remove a wallet from the watchlist."""
    _wm = _L("app.domains.wallet.wallet_monitor")
    manager = _wm.WalletMonitorManager()
    await manager.remove_watch(address, chain)
    return {"unwatched": True, "address": address, "chain": chain}


@router.get("/monitor/watches")
async def monitor_list() -> dict[str, Any]:
    """List all watched wallets."""
    _wm = _L("app.domains.wallet.wallet_monitor")
    manager = _wm.WalletMonitorManager()
    watches = await manager.list_watches()
    return {"watches": watches, "count": len(watches)}


# ─── EXCHANGE FLOW ANALYZER ─────────────────────────────────


@router.post("/flows/analyze")
async def flows_analyze(
    entity_addresses: list[str],
    entity_name: str,
    entity_id: str,
    chain: str,
    transactions: list[dict[str, Any]],
    hours: int = 24,
) -> Any:
    """Analyze fund flows for an entity."""
    _efa = _L("app.exchange_flow_analyzer")
    return await _efa.analyze_entity_flows(entity_addresses, entity_name, entity_id, chain, transactions, hours)


@router.post("/flows/reserves")
async def flows_reserves(
    entity_addresses: list[str],
    entity_name: str,
    entity_id: str,
    chain: str,
) -> Any:
    """Get reserve snapshot for entity addresses."""
    _efa = _L("app.exchange_flow_analyzer")
    return await _efa.get_reserves(entity_addresses, entity_name, entity_id, chain)


@router.post("/flows/compare")
async def flows_compare(
    entity_a: dict[str, Any],
    entity_b: dict[str, Any],
    transactions: list[dict[str, Any]],
) -> Any:
    """Compare fund flows between two entities."""
    _efa = _L("app.exchange_flow_analyzer")
    return await _efa.compare_flows(entity_a, entity_b, transactions)


# ─── HEALTH ────────────────────────────────────────────────


@router.get("/health")
async def security_health() -> dict[str, Any]:
    """Full subsystem health check."""

    # Check all modules
    modules = {}

    try:
        from app.onchain_analyzer import get_w3

        w3 = get_w3("ethereum")  # type: ignore[func-returns-value]
        modules["onchain_analyzer"] = "connected" if w3 and w3.is_connected() else "disconnected"
    except Exception as e:
        modules["onchain_analyzer"] = f"error: {str(e)[:30]}"

    try:
        import sklearn

        modules["ml_anomaly"] = f"sklearn {sklearn.__version__}"
    except ImportError:
        modules["ml_anomaly"] = "sklearn not installed"

    try:
        import slither_analyzer

        modules["contract_deepscan"] = f"slither {slither_analyzer.__version__}"
    except ImportError:
        modules["contract_deepscan"] = "slither not installed"

    # Redis
    try:
        r = await _get_redis()
        modules["redis"] = "connected" if r and await r.ping() else "disconnected"
    except (ConnectionError, TimeoutError, OSError):
        modules["redis"] = "disconnected"

    return {
        "status": "operational",
        "modules": modules,
        "version": "2.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# Redis helper for health check
async def _get_redis() -> Any | None:
    try:
        import redis.asyncio as redis_async

        return redis_async.Redis(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
        )
    except (ConnectionError, TimeoutError, OSError):
        return None
