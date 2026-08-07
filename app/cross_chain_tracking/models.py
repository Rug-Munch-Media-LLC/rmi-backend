"""
Cross-Chain Tracking — Pydantic v2 Data Models
================================================
Canonical wire formats for cross-chain entity resolution results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from datetime import datetime


class DeployerEvent(BaseModel):
    """A single deployment event from chain-indexer's ClickHouse."""

    deployer: str = Field(..., description="Deployer wallet address")
    token_address: str = Field(..., description="Deployed token address")
    chain: str = Field(..., description="Chain where deployed")
    block_number: int = 0
    block_timestamp: str = ""
    tx_hash: str = ""
    type: str = "token"  # token, pool, factory
    factory: str | None = None
    ingested_at: str = ""


class DeployerSummary(BaseModel):
    """Aggregated deployer info across a single chain."""

    deployer: str
    chain: str
    deployment_count: int = 0
    first_deploy: datetime | None = None
    last_deploy: datetime | None = None
    token_types: list[str] = Field(default_factory=list)


class MultiChainDeployer(BaseModel):
    """A deployer active on 2+ chains (same-entity signal)."""

    deployer_address: str = Field(..., description="The EVM/SVM address (same across chains)")
    chains: list[str] = Field(..., description="Chains where this deployer is active")
    chain_count: int = 0
    total_deployments: int = 0
    deployments_by_chain: dict[str, int] = Field(default_factory=dict)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    evidence: str = ""


class CrossChainMatch(BaseModel):
    """A single cross-chain address match with evidence."""

    address: str
    chain: str
    confidence: float = Field(ge=0.0, le=1.0)
    method: Literal[
        "same_deployer",
        "evm_address_portability",
        "bridge_mapping",
        "behavioral_fingerprint",
        "fund_flow_trace",
        "entity_membership",
        "cex_deposit_pattern",
    ] = "same_deployer"
    evidence: str = ""
    matched_at: str = ""


class CrossChainProfile(BaseModel):
    """Complete cross-chain identity profile for an entity."""

    entity_id: str
    primary_address: str
    primary_chain: str
    addresses: list[CrossChainMatch] = Field(default_factory=list)
    total_addresses: int = 0
    total_chains: int = 0
    chains: list[str] = Field(default_factory=list)
    deployments: list[DeployerEvent] = Field(default_factory=list)
    total_deployments: int = 0
    risk_score: float = 0.0
    label: str = ""
    first_seen: str = ""
    last_seen: str = ""


class CrossChainSummary(BaseModel):
    """Summary of cross-chain tracking results."""

    total_deployers_scanned: int = 0
    multi_chain_deployers_found: int = 0
    bridge_links: int = 0
    entities_created: int = 0
    edges_created: int = 0
    chains_with_data: list[str] = Field(default_factory=list)
    new_entity_ids: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    errors: list[str] = Field(default_factory=list)
