"""
Bubble map data models — nodes, links, and map container.

All dataclasses for the interactive wallet visualization engine.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _format_timestamp(ts: int | float | datetime | None) -> str | None:
    """Format a timestamp to ISO string, handling both numeric and datetime types."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


@dataclass
class BubbleNode:
    """A node in the bubble map."""

    id: str
    address: str
    type: str  # center, scammer, exchange, whale, bot, unknown

    # Visual properties
    x: float = 0.0
    y: float = 0.0
    radius: float = 20.0
    color: str = "#69db7c"

    # Data
    label: str = ""
    total_volume: float = 0.0
    transaction_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    # Risk
    risk_score: float = 0.0
    risk_level: str = "unknown"

    # Connections
    connected_to: list[str] = field(default_factory=list)

    # Metadata
    entity_name: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "address": self.address,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "radius": self.radius,
            "color": self.color,
            "label": self.label or f"{self.address[:6]}...{self.address[-4:]}",
            "volume": self.total_volume,
            "transactions": self.transaction_count,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "entity_name": self.entity_name,
            "tags": self.tags,
        }


@dataclass
class BubbleLink:
    """A link between nodes."""

    source: str
    target: str

    # Visual properties
    strength: float = 0.5
    width: float = 2.0
    color: str = "#00d4ff"

    # Data
    total_volume: float = 0.0
    transaction_count: int = 0
    first_tx: datetime | None = None
    last_tx: datetime | None = None

    # Transaction details (for tooltip)
    transactions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "strength": self.strength,
            "width": self.width,
            "color": self.color,
            "volume": self.total_volume,
            "transactions": self.transaction_count,
        }


@dataclass
class BubbleMap:
    """Complete bubble map data."""

    map_id: str
    center_wallet: str
    created_at: datetime

    nodes: list[BubbleNode] = field(default_factory=list)
    links: list[BubbleLink] = field(default_factory=list)

    # Settings
    depth: int = 2
    min_strength: float = 0.1
    time_range: tuple[datetime, datetime] | None = None

    # Stats
    total_volume: float = 0.0
    total_transactions: int = 0
    unique_wallets: int = 0

    def to_dict(self) -> dict:
        return {
            "map_id": self.map_id,
            "center_wallet": self.center_wallet,
            "created_at": self.created_at.isoformat(),
            "settings": {"depth": self.depth, "min_strength": self.min_strength},
            "stats": {
                "nodes": len(self.nodes),
                "links": len(self.links),
                "total_volume": self.total_volume,
                "total_transactions": self.total_transactions,
                "unique_wallets": len(self.nodes),
            },
            "nodes": [n.to_dict() for n in self.nodes],
            "links": [line_list.to_dict() for line_list in self.links],
        }
