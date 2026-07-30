"""Scanner data models and constants.

NOTE: ``ScanResult`` here is the legacy tier-based result model (used by
the old ``service/scan.py``).  New code should prefer the field-based
``ScanResult`` from ``app.domains.scanner.unified_token_scanner``
(DataBus-powered).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ScanResult:
    """Result of a token security scan.

    .. deprecated::
        Prefer ``app.domains.scanner.unified_token_scanner.ScanResult``
        (field-based, DataBus-powered) for new implementations.
    """

    token_address: str
    chain: str
    symbol: str = ""
    name: str = ""
    scanned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # FREE tier
    free: dict[str, Any] = field(default_factory=dict)
    # PRO tier
    pro: dict[str, Any] = field(default_factory=dict)
    # ELITE tier
    elite: dict[str, Any] = field(default_factory=dict)

    safety_score: int = 50  # 0-100, higher = safer (starts at 50 = unknown)
    risk_flags: list[str] = field(default_factory=list)
    tier_required: str = "free"
    confidence: int = 0  # 0-100, how much data backs this score
    modules_run: list[dict[str, Any]] = field(default_factory=list)  # [{module, status, error?}]
    fusion_brief: dict | None = None  # DataFusion enrichment: fused risk + intelligence brief
    rag_context: dict | None = None  # RAG enrichment context (from RAGIntelligenceEnricher)


CHAIN_IDS = {
    "solana": "solana",
    "ethereum": "1",
    "base": "8453",
    "bsc": "56",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "fantom": "250",
    "linea": "59144",
    "zksync": "324",
    "scroll": "534352",
    "mantle": "5000",
}

SOLANA_RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.syndica.io/access-token/free",
]
