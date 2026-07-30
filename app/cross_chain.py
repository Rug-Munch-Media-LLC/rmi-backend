"""
Cross-Chain Entity Resolution — Behavioral fingerprinting across blockchains.
=================================================================================
Answers: "This ETH scammer = this Solana address = this BSC deployer."
Uses behavioral fingerprinting + funding source graph + deployer matching to
link identities across chains.

This module is a thin facade that delegates to the cross_chain_tracking package.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Chain normalization ──────────────────────────────────────────
CHAIN_ALIASES = {
    "ethereum": ["eth", "ethereum", "mainnet", "1"],
    "bsc": ["bsc", "bnb", "binance", "56"],
    "polygon": ["polygon", "matic", "137"],
    "arbitrum": ["arbitrum", "arb", "42161"],
    "optimism": ["optimism", "op", "10"],
    "avalanche": ["avalanche", "avax", "43114"],
    "fantom": ["fantom", "ftm", "250"],
    "base": ["base", "8453"],
    "solana": ["solana", "sol", "mainnet-beta"],
    "tron": ["tron", "trx"],
    "bitcoin": ["bitcoin", "btc"],
}

CHAIN_FROM_ALIAS = {}
for canonical, aliases in CHAIN_ALIASES.items():
    for alias in aliases:
        CHAIN_FROM_ALIAS[alias.lower()] = canonical


def normalize_chain(chain: str) -> str:
    """Normalize chain name to canonical form - delegates to shared_utils."""
    from app.core.shared_utils import normalize_chain as _normalize
    return _normalize(chain)


# ── Behavioral Fingerprint ───────────────────────────────────────
# 8-dim vector: [tx_frequency, gas_volatility, contract_interaction_ratio,
#                avg_tx_value, timezone_activity, dex_ratio, bridge_usage, age_days]

def build_cross_chain_fingerprint(
    tx_frequency: float = 0,
    gas_volatility: float = 0,
    contract_ratio: float = 0,
    avg_tx_value: float = 0,
    timezone_hours: float = 12,
    dex_ratio: float = 0,
    bridge_usage: int = 0,
    age_days: int = 0,
) -> np.ndarray:
    """Build an 8-dim behavioral fingerprint for cross-chain matching."""
    return np.array(
        [
            min(tx_frequency, 50) / 50.0,
            min(gas_volatility, 1.0),
            contract_ratio,
            min(avg_tx_value, 10.0) / 10.0,
            timezone_hours / 24.0,
            dex_ratio,
            min(bridge_usage, 20) / 20.0,
            min(age_days, 1000) / 1000.0,
        ],
        dtype=np.float32,
    )


# ── Funding Source Graph ─────────────────────────────────────────

def analyze_funding_chain(
    address: str,
    funding_sources: list[dict[str, Any]],
    chain: str = "ethereum",
) -> dict[str, Any]:
    """
    Analyze the funding source graph for cross-chain signals.

    funding_sources: list of {address, chain, amount, hop_distance}
    """
    chains_touched = set()
    addresses_found = []
    mixer_signal = False
    cex_signal = False
    cross_chain_hops = 0

    for source in funding_sources:
        src_chain = normalize_chain(source.get("chain", chain))
        chains_touched.add(src_chain)
        addresses_found.append(source.get("address", ""))

        notes = str(source.get("label", "")).lower()
        if any(w in notes for w in ["tornado", "mixer", "privacy"]):
            mixer_signal = True
        if any(w in notes for w in ["binance", "coinbase", "kraken", "exchange", "cex"]):
            cex_signal = True

        if src_chain != normalize_chain(chain):
            cross_chain_hops += 1

    return {
        "chains_involved": list(chains_touched),
        "cross_chain_hops": cross_chain_hops,
        "mixer_funded": mixer_signal,
        "cex_funded": cex_signal,
        "funding_addresses": addresses_found[:10],
        "complexity": "high" if cross_chain_hops > 2 else "medium" if cross_chain_hops > 0 else "low",
    }


# ── Identity Resolution ──────────────────────────────────────────

def resolve_cross_chain_identity(
    address: str,
    chain: str,
    behavioral_fingerprint: np.ndarray | None = None,
    funding_sources: list[dict[str, Any]] | None = None,
    label_hints: list[str] | None = None,
    similar_addresses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Resolve cross-chain identity for a given address.
    Uses on-chain data via cross_chain_tracking package + behavioral signals.
    """
    chain = normalize_chain(chain)
    matches = []
    evidence = []

    # 1. Behavioral fingerprint matching (existing)
    if behavioral_fingerprint is not None:
        for match in similar_addresses or []:
            m_addr = match.get("address", "")
            m_chain = normalize_chain(match.get("chain", ""))
            m_fp = match.get("fingerprint")

            if m_chain == chain:
                continue

            if m_fp is not None:
                m_vec = np.array(m_fp, dtype=np.float32)
                if len(m_vec) == len(behavioral_fingerprint):
                    sim = float(
                        np.dot(behavioral_fingerprint, m_vec)
                        / (np.linalg.norm(behavioral_fingerprint) * np.linalg.norm(m_vec) + 1e-8)
                    )
                    if sim > 0.7:
                        matches.append({
                            "address": m_addr,
                            "chain": m_chain,
                            "confidence": round(sim * 100),
                            "method": "behavioral_fingerprint",
                            "evidence": f"Behavioral fingerprint similarity: {sim:.2f}",
                        })
                        evidence.append(f"Behavioral match: {m_addr} on {m_chain} ({sim:.1%})")

    # 2. Deployer-based matching (NEW — powered by cross_chain_tracking)
    try:
        import asyncio

        from app.cross_chain_tracking.deployer_matcher import DeployerMatcher

        matcher = DeployerMatcher()
        deployer_profile = asyncio.run(matcher.resolve_deployer(address, chain))
        if deployer_profile.total_chains > 1:
            for addr in deployer_profile.addresses:
                if addr.address != address.lower() or addr.chain != chain:
                    matches.append({
                        "address": addr.address,
                        "chain": addr.chain,
                        "confidence": int(addr.confidence * 100),
                        "method": "same_deployer",
                        "evidence": addr.evidence,
                    })
                    evidence.append(
                        f"Same deployer on {addr.chain}: {addr.evidence}"
                    )
    except Exception as e:
        logger.debug("Deployer matcher failed for %s: %s", address[:12], e)

    # 3. Funding source graph analysis
    if funding_sources:
        funding_analysis = analyze_funding_chain(address, funding_sources, chain)
        evidence.append(f"Funding touches {len(funding_analysis['chains_involved'])} chains")
        if funding_analysis["mixer_funded"]:
            evidence.append("Mixer-funded - possible laundering")
        if funding_analysis["cross_chain_hops"] > 0:
            evidence.append(f"{funding_analysis['cross_chain_hops']} cross-chain funding hops detected")

    # 4. Label-based hints
    if label_hints:
        for hint in label_hints[:3]:
            evidence.append(f"Label match: {hint}")

    # ── Aggregate ──
    resolved = bool(matches)
    confidence = max((m["confidence"] for m in matches), default=0)

    return {
        "address": address,
        "chain": chain,
        "resolved": resolved,
        "cross_chain_matches": matches,
        "total_matches": len(matches),
        "max_confidence": confidence,
        "evidence": evidence,
        "funding_analysis": analyze_funding_chain(address, funding_sources or [], chain),
        "summary": (
            f"Found {len(matches)} cross-chain matches for {address[:10]}... on {chain}"
            if matches
            else f"No cross-chain identity matches found for {address[:10]}... on {chain}"
        ),
    }


# ── Quick utility: detect chain from address format ──────────────

def detect_chain(address: str) -> str | None:
    """Detect blockchain from address format."""
    if re.match(r"^0x[a-fA-F0-9]{40}$", address):
        return "ethereum"
    if re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        return "solana"
    if re.match(r"^T[a-zA-Z0-9]{33}$", address):
        return "tron"
    if re.match(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$", address):
        return "bitcoin"
    return None


# ── NEW: Convenience wrappers for cross_chain_tracking ───────────

async def get_cross_chain_profile(address: str, chain: str = "ethereum") -> dict:
    """Get cross-chain entity profile — delegates to tracking package."""
    from app.cross_chain_tracking.entity_tracker import get_entity_tracker
    tracker = get_entity_tracker()
    profile = await tracker.resolve(address, chain)
    return profile.model_dump(mode="json")


async def get_multi_chain_deployers(
    min_chains: int = 2,
    chain_filter: list[str] | None = None,
    limit: int = 5000,
) -> list[dict]:
    """Get deployers active on multiple chains."""
    from app.cross_chain_tracking.deployer_matcher import DeployerMatcher
    matcher = DeployerMatcher()
    results = await matcher.find_multi_chain_deployers(
        min_chains=min_chains,
        chain_filter=chain_filter,
        limit=limit,
    )
    return [r.model_dump(mode="json") for r in results]
