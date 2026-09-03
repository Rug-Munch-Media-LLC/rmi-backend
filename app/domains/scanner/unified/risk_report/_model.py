"""Data model + factor weights for the risk_report package.

Defines the ``RiskFactor`` / ``ClusterInfo`` / ``RiskReport`` dataclasses
(including JSON round-trip via ``to_dict`` / ``from_dict``), the shared risk
thresholds, and the per-factor weights / labels used by the composite score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Thresholds (risk bands shared by every factor) ────────────────────────
_CRITICAL_MIN = 70.0  # factor score >= 70 → CRITICAL
_RISK_MIN = 25.0  # factor score >= 25 → RISK (else OK)

# ── Determination bands (composite risk score) ────────────────────────────
_SCAM_MIN = 85.0  # composite >= 85 → scam (or confirmed honeypot)
_RUG_MIN = 70.0  # composite >= 70 → rug (or rugged-dev + bundle)
_RISKY_MIN = 45.0  # composite >= 45 → risky
_MIN_KNOWN_FOR_DETERMINATION = 6  # need >= 6 known factors to declare a verdict

@dataclass
class RiskFactor:
    """One explainable risk factor.

    ``risk_score`` is 0-100 (higher = riskier); ``None`` means the data was
    unavailable and the factor is reported as ``unknown`` (never crashes the
    scan). ``explanation`` states WHY the current value is (or is not) risky.
    """

    key: str
    label: str
    risk_score: float | None = None
    status: str = "unknown"  # "ok" | "risk" | "critical" | "unknown"
    explanation: str = ""


@dataclass
class ClusterInfo:
    """Beyond-market wallet-cluster detection payload."""

    funder_tree: list[dict[str, Any]] = field(default_factory=list)
    shared_deployer_network: list[dict[str, Any]] = field(default_factory=list)
    cross_token_clusters: list[dict[str, Any]] = field(default_factory=list)
    # Deepened structures (risk_cluster legs 4-8).
    cross_token_funders: list[dict[str, Any]] = field(default_factory=list)
    temporal_clusters: list[dict[str, Any]] = field(default_factory=list)
    sniper_patterns: list[dict[str, Any]] = field(default_factory=list)
    mev_bundles: list[dict[str, Any]] = field(default_factory=list)
    wash_clusters: dict[str, Any] = field(default_factory=dict)
    cluster_risk_score: int | None = None  # 0-100, None = unavailable
    cluster_risk_label: str = "unknown"  # LOW/MEDIUM/HIGH/CRITICAL/unknown
    description: str = ""


@dataclass
class RiskReport:
    """The complete structured scanner output for one token."""

    token_address: str
    chain: str
    scanned_at: str = ""
    tier: str = ""

    # ── Sections ──
    deployer: dict[str, Any] = field(default_factory=dict)
    holders: dict[str, Any] = field(default_factory=dict)
    first_buyer: dict[str, Any] = field(default_factory=dict)
    top_25_holders: list[dict[str, Any]] = field(default_factory=list)
    fresh_wallet_pct: float | None = None
    cex_funded_wallet_pct: float | None = None
    bundle: dict[str, Any] = field(default_factory=dict)
    cluster: ClusterInfo = field(default_factory=ClusterInfo)
    honeypot: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)

    # ── Score + verdict ──
    factors: list[RiskFactor] = field(default_factory=list)
    composite_score: int | None = None  # 0-100 risk, None when nothing known
    determination: str = "unknown"  # "scam" | "rug" | "risky" | "ok" | "unknown"
    summary: str = ""

    # ── Deeper-level expanded analysis (per-factor natural-language) ──
    deeper_analysis: dict[str, Any] = field(default_factory=dict)
    ai_assisted: bool = False  # set True when an AI pass is applied downstream

    # ── Data quality ──
    data_quality: dict[str, Any] = field(default_factory=dict)
    sources_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (used for persistence / API output)."""
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "scanned_at": self.scanned_at,
            "tier": self.tier,
            "deployer": self.deployer,
            "holders": self.holders,
            "first_buyer": self.first_buyer,
            "top_25_holders": self.top_25_holders,
            "fresh_wallet_pct": self.fresh_wallet_pct,
            "cex_funded_wallet_pct": self.cex_funded_wallet_pct,
            "bundle": self.bundle,
            "cluster": {
                "funder_tree": self.cluster.funder_tree,
                "shared_deployer_network": self.cluster.shared_deployer_network,
                "cross_token_clusters": self.cluster.cross_token_clusters,
                "cross_token_funders": self.cluster.cross_token_funders,
                "temporal_clusters": self.cluster.temporal_clusters,
                "sniper_patterns": self.cluster.sniper_patterns,
                "mev_bundles": self.cluster.mev_bundles,
                "wash_trading": self.cluster.wash_clusters,
                "cluster_risk_score": self.cluster.cluster_risk_score,
                "cluster_risk_label": self.cluster.cluster_risk_label,
                "description": self.cluster.description,
            },
            "honeypot": self.honeypot,
            "liquidity": self.liquidity,
            "factors": [
                {
                    "key": f.key,
                    "label": f.label,
                    "risk_score": f.risk_score,
                    "status": f.status,
                    "explanation": f.explanation,
                }
                for f in self.factors
            ],
            "composite_score": self.composite_score,
            "determination": self.determination,
            "summary": self.summary,
            "deeper_analysis": self.deeper_analysis,
            "ai_assisted": self.ai_assisted,
            "data_quality": self.data_quality,
            "sources_used": self.sources_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskReport:
        """Rehydrate a ``RiskReport`` from ``to_dict()`` output (pure round-trip).

        Consumers that receive the serialized report — API responses, the
        Redis result cache, the Telegram bot formatter — can rebuild the typed
        dataclass without re-running the scanner. Defensive: missing keys fall
        back to the dataclass defaults, so a truncated payload never raises.
        """
        cluster_raw = data.get("cluster")
        cluster_src: dict[str, Any] = cluster_raw if isinstance(cluster_raw, dict) else {}
        cluster = ClusterInfo(
            funder_tree=list(cluster_src.get("funder_tree") or []),
            shared_deployer_network=list(cluster_src.get("shared_deployer_network") or []),
            cross_token_clusters=list(cluster_src.get("cross_token_clusters") or []),
            cross_token_funders=list(cluster_src.get("cross_token_funders") or []),
            temporal_clusters=list(cluster_src.get("temporal_clusters") or []),
            sniper_patterns=list(cluster_src.get("sniper_patterns") or []),
            mev_bundles=list(cluster_src.get("mev_bundles") or []),
            wash_clusters=dict(cluster_src.get("wash_trading") or {}),
            cluster_risk_score=cluster_src.get("cluster_risk_score"),
            cluster_risk_label=str(cluster_src.get("cluster_risk_label") or "unknown"),
            description=str(cluster_src.get("description") or ""),
        )
        factors = [
            RiskFactor(
                key=str(f.get("key") or "unknown"),
                label=str(f.get("label") or f.get("key") or "unknown"),
                risk_score=f.get("risk_score"),
                status=str(f.get("status") or "unknown"),
                explanation=str(f.get("explanation") or ""),
            )
            for f in data.get("factors") or []
            if isinstance(f, dict)
        ]
        return cls(
            token_address=str(data.get("token_address") or ""),
            chain=str(data.get("chain") or ""),
            scanned_at=str(data.get("scanned_at") or ""),
            tier=str(data.get("tier") or ""),
            deployer=dict(data.get("deployer") or {}),
            holders=dict(data.get("holders") or {}),
            first_buyer=dict(data.get("first_buyer") or {}),
            top_25_holders=list(data.get("top_25_holders") or []),
            fresh_wallet_pct=data.get("fresh_wallet_pct"),
            cex_funded_wallet_pct=data.get("cex_funded_wallet_pct"),
            bundle=dict(data.get("bundle") or {}),
            cluster=cluster,
            honeypot=dict(data.get("honeypot") or {}),
            liquidity=dict(data.get("liquidity") or {}),
            factors=factors,
            composite_score=data.get("composite_score"),
            determination=str(data.get("determination") or "unknown"),
            summary=str(data.get("summary") or ""),
            deeper_analysis=dict(data.get("deeper_analysis") or {}),
            ai_assisted=bool(data.get("ai_assisted")),
            data_quality=dict(data.get("data_quality") or {}),
            sources_used=list(data.get("sources_used") or []),
        )


# ═══════════════════════════════════════════════════════════════════════════
# FACTOR WEIGHTS (composite = weighted avg over known factors)
# ═══════════════════════════════════════════════════════════════════════════

# ~25 factors, one weight per factor. Weights are relative importance within
# the composite (higher = counts more). The decisive factors (honeypot, mint,
# bundle) are weighted most heavily.
FACTOR_WEIGHTS: dict[str, float] = {
    # Deployer (4)
    "deployer_repeat": 2.0,
    "deployer_rugged_before": 3.0,
    "deployer_new_wallet": 1.5,
    "deployer_funding": 1.5,
    # Holders (5)
    "holder_concentration": 2.5,
    "dev_team_share": 2.5,
    "top_holder_whale": 1.5,
    "fresh_wallet_pct": 2.0,
    "cex_funded_wallet_pct": 1.0,
    # Launch (4)
    "first_buyer_deployer": 1.5,
    "first_buyer_bundle": 2.0,
    "bundle_launch": 3.0,
    "sniper_first_buyers": 1.5,
    # Cluster (8)
    "funder_cluster": 2.5,
    "cross_token_cluster": 2.0,
    "shared_deployer_network": 2.5,
    "cross_token_funder": 2.0,
    "temporal_cluster": 2.0,
    "sniper_bot_pattern": 2.0,
    "mev_bundle": 2.5,
    "wash_trading_cluster": 2.5,
    # Honeypot / contract (4)
    "honeypot": 5.0,
    "mint_authority": 3.0,
    "freeze_authority": 2.0,
    "ownership_renounced": 2.0,
    # Liquidity / tax / market (5)
    "lp_locked": 3.0,
    "lp_liquidity_amount": 1.5,
    "buy_sell_tax": 1.5,
    "volume_authenticity": 1.5,
    "holder_overlap_scam": 2.0,
}

FACTOR_LABELS: dict[str, str] = {
    "deployer_repeat": "Repeat deployer",
    "deployer_rugged_before": "Deployer previously rugged",
    "deployer_new_wallet": "Fresh deployer wallet",
    "deployer_funding": "Deployer funding source",
    "holder_concentration": "Holder concentration (top-10)",
    "dev_team_share": "Dev/team wallet share",
    "top_holder_whale": "Whale top holder",
    "fresh_wallet_pct": "Fresh wallet share",
    "cex_funded_wallet_pct": "CEX-funded wallet share",
    "first_buyer_deployer": "First buyer is deployer",
    "first_buyer_bundle": "First buyer in bundle",
    "bundle_launch": "Bundled launch",
    "sniper_first_buyers": "Sniper first-buyers",
    "funder_cluster": "Shared-funder cluster",
    "cross_token_cluster": "Cross-token wallet cluster",
    "shared_deployer_network": "Shared-deployer network",
    "cross_token_funder": "Cross-token funder graph",
    "temporal_cluster": "Lockstep entry (temporal) cluster",
    "sniper_bot_pattern": "Sniper/bot launch pattern",
    "mev_bundle": "MEV bundle buys",
    "wash_trading_cluster": "Wash-trading cluster",
    "honeypot": "Honeypot (cannot sell)",
    "mint_authority": "Mint authority active",
    "freeze_authority": "Freeze authority active",
    "ownership_renounced": "Ownership not renounced",
    "lp_locked": "Liquidity locked",
    "lp_liquidity_amount": "Liquidity amount",
    "buy_sell_tax": "Buy/sell tax",
    "volume_authenticity": "Volume authenticity",
    "holder_overlap_scam": "Holder overlap with known scams",
}
