"""
V1 Entity Cross-Chain — GET /api/v1/entity/{address}/cross-chain
================================================================
Consolidated cross-chain entity profile merging data from multiple sources:

  - Wallet profile (WalletStorage — ClickHouse + Redis, degradation-aware)
  - Entity resolution (EntityResolver — cross-chain wallet linking)
  - Labels (LabelService — 9 label sources with confidence scoring)
  - Risk score (RiskScorer — composite: label + cluster + deployer history)
  - Related addresses (from entity resolution)

Graceful degradation (Win #14):
  - If ClickHouse is down → Redis cache → degraded result
  - If Redis is down → ClickHouse direct → degraded result
  - If both are down → return partial data with available sources
  - Never returns HTTP 500 — missing sections are returned as null/empty
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/entity", tags=["entity-v1"])

# Supported chains for cross-chain aggregation
_SUPPORTED_CHAINS = [
    "ethereum",
    "solana",
    "base",
    "bsc",
    "arbitrum",
    "polygon",
    "optimism",
    "avalanche",
    "fantom",
]


async def _get_wallet_profile(address: str, chain: str) -> dict | None:
    """Get wallet profile from storage (ClickHouse + Redis, degradation-aware)."""
    try:
        from app.wallet_memory.storage import get_storage

        storage = get_storage()
        return await storage.get_wallet_profile(address, chain)
    except Exception as exc:
        logger.debug("wallet_profile_fail chain=%s addr=%s: %s", chain, address[:12], exc)
        return None


async def _resolve_entity(address: str, chain: str) -> dict[str, Any] | None:
    """Resolve cross-chain entity for a wallet address."""
    try:
        from app.wallet_memory.entity_resolver import get_entity_resolver

        resolver = get_entity_resolver()
        return await resolver.resolve(address, chain)
    except Exception as exc:
        logger.debug("entity_resolve_fail addr=%s: %s", address[:12], exc)
        return None


async def _get_labels(address: str, chain: str) -> dict[str, Any] | None:
    """Get unified labels from all federated sources."""
    try:
        from app.wallet_memory.labels import get_label_service

        service = get_label_service()
        return await service.resolve(address, chain)
    except Exception as exc:
        logger.debug("label_resolve_fail addr=%s: %s", address[:12], exc)
        return None


async def _get_risk_score(address: str, chain: str, intel: dict | None = None) -> dict[str, Any] | None:
    """Get composite risk score for a wallet."""
    try:
        from app.wallet_memory.risk_scoring import RiskScorer

        scorer = RiskScorer()
        return await scorer.score(address, chain, intel=intel or {})
    except Exception as exc:
        logger.debug("risk_score_fail addr=%s: %s", address[:12], exc)
        return None


async def _get_chain_profile(address: str, chain: str) -> dict[str, Any]:
    """Get single-chain profile fragment.

    Returns:
        {
            "balance": str | null,
            "tx_count": int,
            "first_seen": str | null,
            "last_seen": str | null,
            "risk_score": float | null,
            "risk_level": str | null,
        }
    """
    profile = await _get_wallet_profile(address, chain)
    if profile:
        return {
            "balance": str(profile.get("total_volume", "")),
            "tx_count": profile.get("total_transactions", 0),
            "first_seen": (str(profile.get("first_seen_at")) if profile.get("first_seen_at") else None),
            "last_seen": (str(profile.get("last_seen_at")) if profile.get("last_seen_at") else None),
            "risk_score": profile.get("risk_score"),
            "risk_level": profile.get("risk_level"),
        }

    # No ClickHouse profile found — return minimal entry
    return {
        "balance": None,
        "tx_count": 0,
        "first_seen": None,
        "last_seen": None,
        "risk_score": None,
        "risk_level": None,
    }


async def _get_deployer_history(address: str) -> list[dict[str, Any]]:
    """Get deployer history for a wallet across all chains."""
    try:
        from app.wallet_memory.storage import get_storage

        storage = get_storage()
        return await storage.get_deployer_history(address)
    except Exception as exc:
        logger.debug("deployer_history_fail addr=%s: %s", address[:12], exc)
        return []


@router.get("/{address}/cross-chain")
async def entity_cross_chain(address: str) -> dict[str, Any]:
    """Consolidated cross-chain entity profile.

    Aggregates wallet profiles, entity resolution, labels, risk scores,
    and related addresses across all supported chains.

    Graceful degradation:
      - If a single chain fails, it is omitted from profiles (not 500)
      - If entity resolution fails, entity is returned as null
      - If label resolution fails, tags are returned as empty
      - Never returns HTTP 500
    """
    addr = address.lower().strip()

    # ── Stage 1: Determine primary chain ──────────────────────────
    # Try to determine if this looks like an EVM or Solana address
    is_evm = addr.startswith("0x") and len(addr) == 42
    is_solana = not is_evm and len(addr) >= 32 and len(addr) <= 44
    primary_chain = "ethereum" if is_evm else "solana" if is_solana else "ethereum"

    # ── Stage 2: Fan-out across all chains ────────────────────────
    # Get wallet profiles for all chains in parallel
    chain_profiles: dict[str, Any] = {}
    profile_tasks = {chain: asyncio.create_task(_get_chain_profile(addr, chain)) for chain in _SUPPORTED_CHAINS}
    for chain, task in profile_tasks.items():
        try:
            result = await task
            if result and result.get("tx_count", 0) > 0:
                chain_profiles[chain] = result
        except Exception as exc:
            logger.debug("chain_profile_fail chain=%s: %s", chain, exc)
            continue

    # If no chain profiles found, at least add the primary chain
    if not chain_profiles:
        chain_profiles[primary_chain] = {
            "balance": None,
            "tx_count": 0,
            "first_seen": None,
            "last_seen": None,
            "risk_score": None,
            "risk_level": None,
        }

    # ── Stage 3: Entity resolution ────────────────────────────────
    entity = await _resolve_entity(addr, primary_chain)

    # ── Stage 4: Labels ──────────────────────────────────────────
    labels = await _get_labels(addr, primary_chain)

    # Extract tags from labels
    tags: list[str] = []
    if labels:
        if labels.get("is_exchange"):
            tags.append("exchange")
        if labels.get("is_mev_bot"):
            tags.append("mev_bot")
        if labels.get("is_known_scam"):
            tags.append("known_scam")
        if labels.get("is_defi_protocol"):
            tags.append("defi")
        if labels.get("is_bridge"):
            tags.append("bridge")
        if labels.get("is_sanctioned"):
            tags.append("sanctioned")

    # Merge label category into primary_label
    primary_label = labels.get("primary_label", "") if labels else ""
    entity_info: dict[str, Any] | None = None
    if entity and entity.get("entity_id"):
        entity_info = {
            "entity_id": entity["entity_id"],
            "type": labels.get("primary_category", "unknown") if labels else "unknown",
            "name": primary_label or entity.get("entity_id", ""),
            "confidence": _max_confidence(entity),
        }

    # ── Stage 5: Risk score ───────────────────────────────────────
    intel_data: dict[str, Any] = {}
    if labels:
        intel_data["labels"] = labels.get("labels", [])
        intel_data["scam_associations"] = [{"type": "sanctioned"}] if labels.get("is_sanctioned") else []
        intel_data["risk_contribution"] = labels.get("risk_contribution", 0)

    if entity:
        linked = [a for a in entity.get("addresses", []) if a.get("address") != addr]
        intel_data["linked_wallets"] = linked
        intel_data["cross_chain_presence"] = list({a["chain"] for a in entity.get("addresses", [])})
        intel_data["confidence"] = _max_confidence(entity)

    risk = await _get_risk_score(addr, primary_chain, intel=intel_data)

    # ── Stage 6: Related addresses ────────────────────────────────
    related_addresses: list[str] = []
    if entity:
        related_addresses = sorted(
            {
                a["address"]
                for a in entity.get("addresses", [])
                if a["address"] != addr and a.get("confidence", 0) >= 0.5
            }
        )

    # ── Stage 7: Deployer history ─────────────────────────────────
    deployer_history = await _get_deployer_history(addr)

    # ── Compose response ──────────────────────────────────────────
    risk_payload: dict[str, Any] = {
        "score": risk.get("score", 0) if risk else 0,
        "tier": risk.get("level", "unknown") if risk else "unknown",
        "factors": [],
    }
    if risk and risk.get("warnings"):
        risk_payload["factors"] = risk["warnings"]
    if risk and risk.get("components"):
        risk_payload["components"] = risk["components"]

    response: dict[str, Any] = {
        "address": addr,
        "primary_chain": primary_chain,
        "profiles": chain_profiles,
        "entity": entity_info,
        "risk": risk_payload,
        "related_addresses": related_addresses,
        "tags": tags,
    }

    if deployer_history:
        response["deployer_history"] = deployer_history

    return response


def _max_confidence(entity: dict[str, Any]) -> float:
    """Get the maximum confidence score across all resolved addresses."""
    addresses = entity.get("addresses", [])
    if not addresses:
        return 0.0
    return max(a.get("confidence", 0) for a in addresses)
