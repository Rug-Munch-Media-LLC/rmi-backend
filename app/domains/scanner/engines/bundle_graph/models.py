"""Typed shapes for the bundle graph.

Every field is evidence-bearing: a wallet knows when it first bought and what
it still holds; an edge knows which transaction proves it and which data
source produced it; a cluster knows how much supply it controls and how much
it has already realised. Nothing here is a bare score -- the score is derived
in :mod:`confidence` from the signals that actually fired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EdgeKind = Literal[
    "same_funder",
    "cex_withdrawal",
    "bridge_in",
    "first_co_occurrence",
]

#: Signal names in the order they are reported. Each one is an independent
#: observation; ``confidence`` needs at least two before it calls a bundle.
SIGNAL_NAMES = (
    "same_slot_buys",
    "early_slot_cluster",
    "shared_funder",
    "cex_within_window",
    "bridge_in",
    "similar_size",
    "creator_funded",
)


@dataclass(frozen=True)
class WalletPosition:
    """One wallet position in the launch window, derived from trade legs."""

    address: str
    first_slot: int
    first_ts: float
    buy_count: int = 0
    sell_count: int = 0
    tokens_bought_raw: int = 0
    tokens_sold_raw: int = 0
    usd_spent: float = 0.0
    usd_received: float = 0.0
    is_creator: bool = False

    @property
    def net_tokens_raw(self) -> int:
        """Tokens still held from the launch window (never negative)."""
        return max(0, self.tokens_bought_raw - self.tokens_sold_raw)

    @property
    def still_holding(self) -> bool:
        """True when the wallet kept at least 5% of what it bought."""
        if self.tokens_bought_raw <= 0:
            return False
        return self.net_tokens_raw / self.tokens_bought_raw >= 0.05

    @property
    def realised_usd(self) -> float:
        """Cash returned by sells (no cost-basis claim is made)."""
        return round(max(0.0, self.usd_received), 2)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe payload."""
        return {
            "address": self.address,
            "first_slot": self.first_slot,
            "first_ts": self.first_ts,
            "buys": self.buy_count,
            "sells": self.sell_count,
            "tokens_bought_raw": self.tokens_bought_raw,
            "tokens_sold_raw": self.tokens_sold_raw,
            "net_tokens_raw": self.net_tokens_raw,
            "usd_spent": round(self.usd_spent, 2),
            "realised_usd": self.realised_usd,
            "still_holding": self.still_holding,
            "is_creator": self.is_creator,
        }


@dataclass(frozen=True)
class BundleEdge:
    """A proven link between two addresses."""

    from_address: str
    to_address: str
    kind: EdgeKind
    evidence_tx: str
    slot: int
    block_ts: float
    provenance: str
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe payload (``from``/``to`` match the public contract)."""
        return {
            "from": self.from_address,
            "to": self.to_address,
            "kind": self.kind,
            "evidence_tx": self.evidence_tx,
            "slot": self.slot,
            "block_ts": self.block_ts,
            "provenance": self.provenance,
            "weight": self.weight,
        }


@dataclass
class BundleCluster:
    """A set of wallets that bought together, with their economics."""

    wallets: list[str]
    first_slot: int
    span_slots: int
    supply_pct: float | None
    realised_usd: float
    realised_sol: float | None
    still_holding: bool
    evidence_kinds: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Number of wallets in the cluster."""
        return len(self.wallets)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe payload."""
        return {
            "size": self.size,
            "wallets": list(self.wallets),
            "first_slot": self.first_slot,
            "span_slots": self.span_slots,
            "supply_pct": self.supply_pct,
            "realised_usd": round(self.realised_usd, 2),
            "realised_sol": self.realised_sol,
            "still_holding": self.still_holding,
            "evidence_kinds": sorted(set(self.evidence_kinds)),
        }


@dataclass
class BundleGraph:
    """The public ``bundle_graph`` contract."""

    token: str
    chain: str
    launch_slot: int | None = None
    window_slots: int = 0
    window_seconds: int = 0
    wallets: list[WalletPosition] = field(default_factory=list)
    edges: list[BundleEdge] = field(default_factory=list)
    clusters: list[BundleCluster] = field(default_factory=list)
    confidence: int = 0
    confidence_tier: str = "none"
    bundle_pct_of_supply: float | None = None
    bundle_pct_of_first_minute_volume: float | None = None
    bundle_pct_of_early_tokens_bought: float | None = None
    signals: list[str] = field(default_factory=list)
    supply_source: str = "unavailable"
    executed_sol_price_usd: float | None = None
    notes: list[str] = field(default_factory=list)
    provider: str = "own_clickhouse"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe payload for the scan response."""
        return {
            "token": self.token,
            "chain": self.chain,
            "launch_slot": self.launch_slot,
            "window_slots": self.window_slots,
            "window_seconds": self.window_seconds,
            "wallets": [w.to_dict() for w in self.wallets],
            "edges": [e.to_dict() for e in self.edges],
            "clusters": [c.to_dict() for c in self.clusters],
            "confidence": self.confidence,
            "confidence_tier": self.confidence_tier,
            "bundle_pct_of_supply": self.bundle_pct_of_supply,
            "bundle_pct_of_first_minute_volume": self.bundle_pct_of_first_minute_volume,
            "bundle_pct_of_early_tokens_bought": self.bundle_pct_of_early_tokens_bought,
            "signals": list(self.signals),
            "supply_source": self.supply_source,
            "executed_sol_price_usd": self.executed_sol_price_usd,
            "notes": list(self.notes),
            "provider": self.provider,
        }
