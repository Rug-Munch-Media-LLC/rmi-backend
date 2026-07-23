"""
Wallet Memory Bank API Router - /api/v1/wallet/*
===================================================
Unified wallet intelligence endpoints for both:
  - WalletSafe frontend (wallet profiles, entity explorer)
  - SENTINEL integration (deployer intelligence)
"""

import contextlib
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger("wallet_memory.router")

router = APIRouter(prefix="/api/v1/wallet", tags=["wallet-memory"])


# ── Request/Response models ────────────────────────────────────


class WalletProfileRequest(BaseModel):
    address: str
    chain: str = "solana"
    depth: int = Field(default=1, ge=1, le=5, description="Cluster exploration depth")


class DeployerIntelRequest(BaseModel):
    address: str
    chain: str = "solana"


class FlagDeployerRequest(BaseModel):
    address: str
    chain: str = "solana"
    reason: str = "high_risk_token"
    token: str = ""
    score: float = Field(default=0, ge=0, le=100)


class SearchRequest(BaseModel):
    query: str
    chain: str = ""
    limit: int = Field(default=20, ge=1, le=100)


class FeedTransactionsRequest(BaseModel):
    transactions: list[dict[str, Any]]
    chain_id: str = "solana"


class BridgeLinkRequest(BaseModel):
    source_address: str
    source_chain: str
    dest_address: str
    dest_chain: str
    bridge_name: str
    tx_hash: str
    confidence: float = Field(default=1.0, ge=0, le=1.0)


# ── Engine helper ────────────────────────────────────────────────


def _get_engine() -> Any:
    from .engine import get_wallet_engine

    return get_wallet_engine()


# ── Endpoints ───────────────────────────────────────────────────


@router.get("/{address}/profile")
async def get_wallet_profile(
    address: str,
    chain: str = Query(default="solana"),
    depth: int = Query(default=1, ge=1, le=5),
) -> Any:
    """
    Get full wallet profile with entity info, risk, labels, and cluster.
    This is the primary WalletSafe product endpoint.
    """
    engine = _get_engine()
    profile = await engine.get_wallet_profile(address, chain, depth)
    return profile


@router.post("/deployer-intel")
async def get_deployer_intelligence(req: DeployerIntelRequest) -> Any:
    """
    Get deployer intelligence for SENTINEL token scans.
    Returns entity, labels, risk, and deployer history in one shot.
    """
    engine = _get_engine()
    intel = await engine.get_deployer_intelligence(req.address, req.chain)
    return intel


@router.post("/flag-deployer")
async def flag_deployer(req: FlagDeployerRequest) -> dict:
    """
    Feed scan results back into the Wallet Memory Bank.
    This is the data flywheel: scans produce intelligence for future scans.
    """
    engine = _get_engine()
    success = await engine.flag_deployer(req.address, req.chain, req.reason, req.token, req.score)
    return {"flagged": success, "address": req.address, "chain": req.chain}


@router.post("/search")
async def search_wallets(req: SearchRequest) -> dict:
    """Search wallets by address prefix, label, or entity name."""
    engine = _get_engine()
    results = await engine.search_wallets(req.query, req.chain, req.limit)
    return {"query": req.query, "results": results, "count": len(results)}


@router.get("/{address}/entity")
async def get_entity(address: str, chain: str = Query(default="solana")) -> Any:
    """
    Get cross-chain entity resolution for a wallet.
    Returns linked addresses across all chains.
    """
    from .entity_resolver import get_entity_resolver

    resolver = get_entity_resolver()
    entity = await resolver.resolve(address, chain)
    return entity


@router.post("/bridge-link")
async def create_bridge_link(req: BridgeLinkRequest) -> dict:
    """
    Explicitly link two wallets across chains via a bridge transaction.
    """
    from .entity_resolver import get_entity_resolver

    resolver = get_entity_resolver()
    entity_id = await resolver.link_via_bridge(
        req.source_address,
        req.source_chain,
        req.dest_address,
        req.dest_chain,
        req.bridge_name,
        req.tx_hash,
        req.confidence,
    )
    return {"entity_id": entity_id, "linked": True}


@router.post("/feed-transactions")
async def feed_transactions(req: FeedTransactionsRequest) -> dict[str, int]:
    """
    Feed transactions into the clustering engine.
    Used by data ingestion pipelines to build entity clusters.
    """
    engine = _get_engine()
    engine.feed_transactions(req.transactions, req.chain_id)
    stats = engine.clustering.get_stats()
    return {"fed": len(req.transactions), "engine_stats": stats}


@router.post("/persist-clusters")
async def persist_clusters() -> dict:
    """Run all clustering heuristics and persist results to storage."""
    engine = _get_engine()
    persisted = await engine.persist_clusters()
    return {"persisted": persisted}


@router.get("/{address}/risk")
async def get_wallet_risk(
    address: str,
    chain: str = Query(default="solana"),
) -> dict:
    """
    Get just the risk score for a wallet (fast path).
    """
    engine = _get_engine()
    intel = await engine.get_deployer_intelligence(address, chain)
    return {
        "address": address.lower(),
        "chain": chain,
        "risk_score": intel.get("risk_score", 0),
        "risk_level": intel.get("risk_level", "unknown"),
        "scam_associations": intel.get("scam_associations", []),
        "warnings": [],
    }


@router.get("/stats")
async def wallet_memory_stats() -> dict[str, str]:
    """Get Wallet Memory Bank statistics."""
    engine = _get_engine()
    cluster_stats = engine.clustering.get_stats()
    return {
        "storage": "clickhouse+redis" if engine.storage._ch else "redis-only",
        "clustering": cluster_stats,
        "label_sources": 9,
    }


@router.get("/health")
async def wallet_memory_health() -> dict[str, str]:
    """Health check for Wallet Memory Bank."""
    engine = _get_engine()
    # Force lazy connection to actually test
    with contextlib.suppress(Exception):
        await engine.storage._ensure_connections()
    storage_ok = engine.storage._redis is not None or engine.storage._ch is not None
    return {
        "status": "ok" if storage_ok else "degraded",
        "storage": {
            "redis": engine.storage._redis is not None,
            "clickhouse": engine.storage._ch is not None,
        },
        "clustering": {
            "wallets": engine.clustering.uf.total_nodes,
            "heuristics": 6,
        },
    }


@router.post("/labels/reload")
async def reload_static_labels() -> dict[str, str]:
    """
    Trigger a full reload of all static wallet labels.
    Re-downloads Solana CSVs if missing, reloads all etherscan CSVs and OFAC data.
    Stores to both Redis and ClickHouse via WalletStorage.
    """
    from .label_importer import is_loading, load_all_static_labels

    if is_loading():
        return {"status": "already_loading", "message": "Label import is already in progress"}
    counts = await load_all_static_labels()
    return {
        "status": "complete",
        "counts": counts,
    }


@router.get("/labels/stats")
async def label_stats() -> dict:
    """
    Get current label load counts and status.
    """
    from .label_importer import get_last_load_counts, is_loading

    return {
        "loading": is_loading(),
        "last_counts": get_last_load_counts(),
    }


# ── Fast Label Lookup ───────────────────────────────────────────


@router.get("/{address}/labels")
async def get_wallet_labels(
    address: str,
    chain: str = Query(default=""),
) -> dict:
    """Fast label lookup - returns all labels for an address instantly."""
    engine = _get_engine()

    # Try known chains
    chains_to_check = [chain] if chain else ["ethereum", "solana", "bitcoin", "tron"]

    labels = []
    for ch in chains_to_check:
        try:
            profile = await engine.get_wallet_profile(address, ch, depth=0)
            if profile and profile.get("labels"):
                labels.extend(profile["labels"])
        except Exception:
            continue

    # Also check threat actor database
    from app.auto_labeler import get_auto_labeler

    labeler = get_auto_labeler()

    # Check if this wallet appears in auto-labeler observations
    for ch in chains_to_check:
        key = f"{ch}:{address.lower()}"
        if key in labeler.pending_observations:
            for obs in labeler.pending_observations.get(key, []):
                if isinstance(obs, dict) and obs.get("label_key"):
                    labels.append(obs)

    return {
        "address": address,
        "chain": chain or "auto",
        "labels": labels,
        "count": len(labels),
        "queried_at": datetime.now(UTC).isoformat(),
    }


@router.post("/labels/batch")
async def batch_label_lookup(request: dict) -> dict[str, int]:
    """Bulk lookup - query up to 100 addresses at once."""
    addresses = request.get("addresses", [])
    chain = request.get("chain", "")

    if len(addresses) > 100:
        raise HTTPException(status_code=400, detail="Max 100 addresses per batch")

    engine = _get_engine()
    results = {}

    for addr in addresses[:100]:
        try:
            profile = await engine.get_wallet_profile(addr, chain or "ethereum", depth=0)
            results[addr] = {
                "labels": profile.get("labels", []) if profile else [],
                "risk_score": profile.get("risk_score", 0) if profile else 0,
            }
        except Exception:
            results[addr] = {"labels": [], "risk_score": 0}

    return {
        "queried": len(addresses),
        "results": results,
        "chain": chain or "auto",
    }


@router.get("/labels/freshness")
async def label_freshness_stats() -> Any:
    """Check how fresh our labels are - when were they last verified."""
    import os

    clean_dir = os.path.join(os.environ.get("RMI_DATA_DIR", "/app/data"), "wallet-labels-clean")
    manifest_path = os.path.join(clean_dir, "manifest.json")

    freshness = {
        "clean_data_generated": None,
        "age_hours": None,
        "status": "unknown",
        "files": {},
    }

    if os.path.exists(manifest_path):
        import json

        with open(manifest_path) as f:
            manifest = json.load(f)
        generated = manifest.get("generated_at")
        if generated:
            gen_time = datetime.fromisoformat(generated).replace(tzinfo=UTC)
            age = (datetime.now(UTC) - gen_time).total_seconds() / 3600
            freshness["clean_data_generated"] = generated
            freshness["age_hours"] = round(age, 1)
            freshness["status"] = "fresh" if age < 24 else "stale" if age < 72 else "critical"

    # Check individual files
    for fname in [
        "wallet_labels_ethereum.csv",
        "wallet_labels_solana.csv",
        "wallet_labels_threat_actors.csv",
    ]:
        fpath = os.path.join(clean_dir, fname)
        if os.path.exists(fpath):
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=UTC)
            freshness["files"][fname] = {
                "size_kb": round(os.path.getsize(fpath) / 1024, 1),
                "last_modified": mtime.isoformat(),
                "age_hours": round((datetime.now(UTC) - mtime).total_seconds() / 3600, 1),
            }

    return freshness
