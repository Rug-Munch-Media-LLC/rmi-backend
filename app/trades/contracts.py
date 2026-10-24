"""Contracts for live trades: the result shape and the injected collaborators.

``TradeDeps`` is the single dependency-injection seam — a frozen value holding
the per-chain source registry and the cache store. Production wiring is
``TradeDeps.default()``; tests and callers pass their own, so nothing has to
patch module attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.trades import cache
from app.trades.gt_trades import serialize
from app.trades.schema import Trade
from app.trades.sources import SourceMap, default_sources


class TradeStore(Protocol):
    """Read/write cache seam (``app.trades.cache`` satisfies it)."""

    async def read(self, chain: str, token: str) -> dict[str, Any] | None: ...

    async def write(self, chain: str, token: str, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class TradeDeps:
    """Injected collaborators: the source registry and the cache store."""

    sources: SourceMap
    store: TradeStore

    @classmethod
    def default(cls) -> TradeDeps:
        """Production wiring: real adapters, Redis cache."""
        return cls(sources=default_sources(), store=cache)


@dataclass
class TradeResult:
    trades: list[Trade] = field(default_factory=list)
    source: str = "none"
    cached: bool = False
    tried: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trades": serialize(self.trades),
            "source": self.source,
            "cached": self.cached,
            "sources_tried": self.tried,
        }
