"""
SENTINEL - Configuration & Report Dataclasses
==============================================
SentinelConfig, SentinelReport, and the dataclass_to_dict utility.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("sentinel_pipeline")

@dataclass
class SentinelConfig:
    """Configuration for SENTINEL pipeline - API keys and shared clients."""

    # API keys
    helius_api_key: str = ""
    birdeye_api_key: str = ""

    # Shared httpx clients (optional - created if not provided)
    dexscreener_client: httpx.AsyncClient | None = None

    def get_dexscreener_client(self) -> httpx.AsyncClient:
        """Return a shared DexScreener httpx client (creates one if needed)."""
        if self.dexscreener_client is None:
            self.dexscreener_client = httpx.AsyncClient(timeout=15.0)
        return self.dexscreener_client

def dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclass instances to dicts.

    Handles:
      - Nested dataclasses
      - Optional fields (None stays None)
      - Lists/tuples of dataclasses
      - Primitive types pass through unchanged
    """
    if obj is None:
        return None

    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}

    if isinstance(obj, (list, tuple)):
        return [dataclass_to_dict(item) for item in obj]

    if isinstance(obj, dict):
        return {k: dataclass_to_dict(v) for k, v in obj.items()}

    return obj

@dataclass
class SentinelReport:
    """Aggregated SENTINEL deep-scan report across all modules."""

    token_address: str
    chain: str
    scanned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Per-module results (None if skipped/failed)
    holder_analysis: dict[str, Any] | None = None
    bundle_detection: dict[str, Any] | None = None
    exchange_funding: dict[str, Any] | None = None
    liquidity_verification: dict[str, Any] | None = None
    dev_reputation: dict[str, Any] | None = None
    wash_trading: dict[str, Any] | None = None
    metadata_fingerprint: dict[str, Any] | None = None
    sentiment: dict[str, Any] | None = None
    pumpfun_analysis: dict[str, Any] | None = None
    honeypot_detection: dict[str, Any] | None = None
    contract_authority: dict[str, Any] | None = None
    mev_detection: dict[str, Any] | None = None
    flash_loan_detection: dict[str, Any] | None = None
    pump_dump_detection: dict[str, Any] | None = None
    oracle_manipulation: dict[str, Any] | None = None
    governance_attack: dict[str, Any] | None = None
    proxy_detection: dict[str, Any] | None = None

    # Tier 3 scanners
    static_analysis: dict[str, Any] | None = None
    decompiler_analysis: dict[str, Any] | None = None
    address_labels: dict[str, Any] | None = None

    # Tier 3.5 - Wallet Memory Bank intelligence
    wallet_intel: dict[str, Any] | None = None

    # Tier 4 - Visualization
    fund_flow: dict[str, Any] | None = None
    contract_diff: dict[str, Any] | None = None

    # Guilt by Association
    guilt_association: dict[str, Any] | None = None

    # Sleep Cycle Analysis
    sleep_cycle: dict[str, Any] | None = None

    # Social Velocity
    social_velocity: dict[str, Any] | None = None

    # Bytecode Similarity
    bytecode_similarity: dict[str, Any] | None = None

    # Block Zero Sniper
    block_zero_sniper: dict[str, Any] | None = None

    # Three Hop Gas Trace
    gas_trace: dict[str, Any] | None = None

    # Module errors (module_name -> error message)
    errors: dict[str, str] = field(default_factory=dict)

    # Which modules actually ran
    modules_run: list[str] = field(default_factory=list)

    # Composite risk score 0-100 (higher = more risky)
    composite_risk_score: float = 0.0
    risk_level: str = "unknown"

    # Aggregated red flags across all modules
    red_flags: list[str] = field(default_factory=list)

    # Per-module risk scores (module_name -> 0-100)
    module_risk_scores: dict[str, float] = field(default_factory=dict)
