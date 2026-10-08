"""Data models for unified data layer."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result from a tool data call with provenance tracking."""

    data: Any
    source: str  # which provider returned the data
    cached: bool = False
    fallback_used: bool = False
    latency_ms: float = 0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "source": self.source,
            "cached": self.cached,
            "fallback": self.fallback_used,
            "latency_ms": round(self.latency_ms, 1),
            "confidence": round(self.confidence, 2),
        }
