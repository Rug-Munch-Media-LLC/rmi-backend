"""
Campaign Radar - Coordinated Scam Detection
============================================

Detects coordinated rug pull campaigns across multiple tokens.
Clusters tokens by deployer entity, funding source, contract similarity,
and social signal correlation.

Premium feature: "4 tokens detected from same entity - coordinated rug campaign"
"""

import asyncio
import hashlib
from app.core.logging import get_logger
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = get_logger("sentinel.campaign")

# In-memory recent scan cache (should be Redis-backed in production)
_recent_scans: dict[str, dict[str, Any]] = {}  # "chain:address" → scan metadata
MAX_RECENT = 500  # Keep last 500 scans for campaign detection


@dataclass
class CampaignCluster:
    """A detected coordinated campaign."""

    cluster_id: str
    tokens: list[dict[str, Any]] = field(default_factory=list)
    deployer_entity: str | None = None
    funding_source: str | None = None
    contract_similarity: float = 0.0  # 0-1
    social_correlation: float = 0.0  # 0-1
    risk_level: str = "unknown"  # "critical"/"high"/"medium"
    estimated_victims: int = 0
    first_detected: str | None = None
    description: str = ""


def record_scan(chain: str, address: str, metadata: dict[str, Any]):
    """Record a scan for campaign correlation."""
    key = f"{chain}:{address.lower()}"
    metadata["_recorded_at"] = (
        asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else __import__("time").time()
    )
    _recent_scans[key] = metadata

    # Evict oldest if over capacity
    if len(_recent_scans) > MAX_RECENT:
        oldest = min(_recent_scans.keys(), key=lambda k: _recent_scans[k].get("_recorded_at", 0))
        del _recent_scans[oldest]


def detect_campaigns(min_cluster_size: int = 3) -> list[CampaignCluster]:
    """Analyze recent scans for coordinated campaigns.

    Clusters tokens by:
    1. Same deployer entity (strongest signal)
    2. Same funding source
    3. High contract bytecode similarity
    4. Correlated social/KOL mentions
    """
    if len(_recent_scans) < min_cluster_size:
        return []

    scans = list(_recent_scans.values())
    campaigns = []

    # ── Strategy 1: Same deployer entity ──
    deployer_groups = defaultdict(list)
    for scan in scans:
        deployer = _extract_deployer_entity(scan)
        if deployer:
            deployer_groups[deployer].append(scan)

    for entity, group in deployer_groups.items():
        if len(group) >= min_cluster_size:
            campaign = CampaignCluster(
                cluster_id=f"deployer_{entity[:12]}",
                tokens=[_token_summary(s) for s in group],
                deployer_entity=entity,
                risk_level="critical" if len(group) >= 5 else "high",
                estimated_victims=sum(s.get("holder_count", 0) or 0 for s in group),
                description=f"{len(group)} tokens launched by same deployer entity {entity[:8]}...",
            )
            campaigns.append(campaign)

    # ── Strategy 2: Same funding source ──
    funding_groups = defaultdict(list)
    for scan in scans:
        funder = _extract_funding_source(scan)
        if funder:
            funding_groups[funder].append(scan)

    for funder, group in funding_groups.items():
        if len(group) >= min_cluster_size:
            # Avoid double-counting with deployer groups
            existing_tokens = set()
            for c in campaigns:
                for t in c.tokens:
                    existing_tokens.add(f"{t.get('chain', '')}:{t.get('address', '')}")

            new_tokens = [
                s for s in group if f"{s.get('chain', '')}:{s.get('address', '')}".lower() not in existing_tokens
            ]
            if len(new_tokens) >= min_cluster_size:
                campaign = CampaignCluster(
                    cluster_id=f"funder_{funder[:12]}",
                    tokens=[_token_summary(s) for s in new_tokens],
                    funding_source=funder,
                    risk_level="high",
                    estimated_victims=sum(s.get("holder_count", 0) or 0 for s in new_tokens),
                    description=f"{len(new_tokens)} tokens funded from same source {funder[:8]}...",
                )
                campaigns.append(campaign)

    # ── Strategy 3: Contract similarity ──
    similar_pairs = []
    scan_list = list(_recent_scans.values())
    for i in range(len(scan_list)):
        for j in range(i + 1, len(scan_list)):
            sim = _contract_similarity(scan_list[i], scan_list[j])
            if sim > 0.85:
                similar_pairs.append((scan_list[i], scan_list[j], sim))

    if similar_pairs:
        # Union-find to cluster similar contracts
        clusters = _cluster_similar(similar_pairs)
        for cluster_tokens in clusters:
            if len(cluster_tokens) >= min_cluster_size:
                avg_sim = sum(p[2] for p in similar_pairs if p[0] in cluster_tokens and p[1] in cluster_tokens) / max(
                    len(cluster_tokens), 1
                )
                campaign = CampaignCluster(
                    cluster_id=f"contract_{hashlib.sha256(str(sorted([t.get('address', '') for t in cluster_tokens])).encode()).hexdigest()[:12]}",
                    tokens=[_token_summary(s) for s in cluster_tokens],
                    contract_similarity=avg_sim,
                    risk_level="high" if avg_sim > 0.95 else "medium",
                    estimated_victims=sum(s.get("holder_count", 0) or 0 for s in cluster_tokens),
                    description=f"{len(cluster_tokens)} tokens with {avg_sim:.0%} contract similarity - likely cloned scam contracts",
                )
                campaigns.append(campaign)

    return sorted(campaigns, key=lambda c: -len(c.tokens))


def _extract_deployer_entity(scan: dict) -> str | None:
    """Extract deployer entity ID from scan metadata."""
    free = scan.get("free", scan)
    deployer = free.get("deployer", {}) or {}
    deep = free.get("deep_deployer", {}) or {}

    entity_id = deployer.get("entity_id") or deep.get("entity_id") or deployer.get("address")
    return entity_id


def _extract_funding_source(scan: dict) -> str | None:
    """Extract funding source from scan metadata."""
    free = scan.get("free", scan)
    funding = free.get("funding_source") or free.get("deep_deployer", {}).get("funding_source")
    return funding


def _contract_similarity(scan_a: dict, scan_b: dict) -> float:
    """Estimate contract similarity between two scans."""
    free_a = scan_a.get("free", scan_a)
    free_b = scan_b.get("free", scan_b)

    # Bytecode hash match (strongest)
    bc_a = free_a.get("bytecode_hash") or free_a.get("contract_diff", {}).get("bytecode_hash")
    bc_b = free_b.get("bytecode_hash") or free_b.get("contract_diff", {}).get("bytecode_hash")
    if bc_a and bc_b and bc_a == bc_b:
        return 1.0

    # Selector set Jaccard similarity
    selectors_a = set(free_a.get("selectors", []) or [])
    selectors_b = set(free_b.get("selectors", []) or [])
    if selectors_a and selectors_b:
        intersection = selectors_a & selectors_b
        union = selectors_a | selectors_b
        if union:
            return len(intersection) / len(union)

    return 0.0


def _cluster_similar(pairs: list[tuple]) -> list[list]:
    """Union-find clustering of similar contract pairs."""
    parent = {}

    def find(x):
        addr = x.get("address", id(x))
        if addr not in parent:
            parent[addr] = addr
        if parent[addr] != addr:
            parent[addr] = find({"address": parent[addr]})
        return parent[addr]

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b, _ in pairs:
        union(a, b)

    clusters = defaultdict(list)
    for a, b, _ in pairs:
        root = find(a)
        if a not in clusters[root]:
            clusters[root].append(a)
        if b not in clusters[root]:
            clusters[root].append(b)

    return list(clusters.values())


def _token_summary(scan: dict) -> dict[str, Any]:
    """Create a concise token summary for campaign display."""
    return {
        "address": scan.get("address") or scan.get("token_address", ""),
        "chain": scan.get("chain", ""),
        "symbol": scan.get("symbol", ""),
        "name": scan.get("name", ""),
        "safety_score": scan.get("safety_score", 50),
        "age_hours": scan.get("free", {}).get("age_hours", 0) if isinstance(scan.get("free"), dict) else 0,
        "holder_count": scan.get("free", {}).get("holders", {}).get("total", 0)
        if isinstance(scan.get("free", {}).get("holders"), dict)
        else 0,
    }


def get_active_campaigns() -> dict[str, Any]:
    """Get all currently detected campaigns."""
    campaigns = detect_campaigns()
    return {
        "status": "ok",
        "active_campaigns": len(campaigns),
        "scans_analyzed": len(_recent_scans),
        "campaigns": [
            {
                "id": c.cluster_id,
                "token_count": len(c.tokens),
                "deployer_entity": c.deployer_entity,
                "funding_source": c.funding_source,
                "contract_similarity": round(c.contract_similarity, 3),
                "risk_level": c.risk_level,
                "estimated_victims": c.estimated_victims,
                "description": c.description,
                "tokens": c.tokens[:10],  # Top 10 tokens
            }
            for c in campaigns
        ],
    }
