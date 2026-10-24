"""Server-side tool registry for the page assistant.

Two rules live here:

1. **Allowlist only.** ``select_tools`` intersects the client's tool hints with
   the alias grant; a name that is not granted never reaches the model, and
   ``is_allowed`` is re-checked before any tool call is executed.
2. **Server-side execution.** The browser never sees a provider or tool key.
   Execution itself lives in :mod:`app.assistant_gateway.executor`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

from app.assistant_gateway.aliases import AliasSpec


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """An OpenAI function definition (name, description, JSON schema)."""

    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai(self) -> dict[str, Any]:
        """Render as an OpenAI ``tools[]`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Normalised tool outcome. ``citations`` backs the RAG citation contract."""

    ok: bool
    data: Any = None
    error: str = ""
    citations: tuple[dict[str, str], ...] = ()


_SCHEMA_ADDRESS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "chain": {"type": "string", "description": "Chain id, e.g. solana, ethereum"},
        "address": {"type": "string", "description": "Token contract address"},
    },
    "required": ["chain", "address"],
    "additionalProperties": False,
}

TOOL_SPECS: Final[dict[str, ToolSpec]] = {
    "scanner_risk": ToolSpec(
        "scanner_risk",
        "Cached risk score and risk flags for a token.",
        _SCHEMA_ADDRESS,
    ),
    "scanner_full": ToolSpec(
        "scanner_full",
        "Full cached scan payload (price, holders, liquidity, risk) for a token.",
        _SCHEMA_ADDRESS,
    ),
    "scanner_price": ToolSpec(
        "scanner_price",
        "Cached price, market cap and volume for a token.",
        _SCHEMA_ADDRESS,
    ),
    "scanner_holders": ToolSpec(
        "scanner_holders",
        "Holder distribution and concentration for a token.",
        _SCHEMA_ADDRESS,
    ),
    "trades_recent": ToolSpec(
        "trades_recent",
        "Recent trades for a token (side, size, price, timestamp).",
        {
            "type": "object",
            "properties": {
                "chain": {"type": "string"},
                "address": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["chain", "address"],
            "additionalProperties": False,
        },
    ),
    "trades_stats": ToolSpec(
        "trades_stats",
        "Aggregate trade statistics for a token (buy/sell imbalance, sizes).",
        _SCHEMA_ADDRESS,
    ),
    "rugmaps_cluster_analysis": ToolSpec(
        "rugmaps_cluster_analysis",
        "Cluster/funding-path analysis for a wallet cluster.",
        {
            "type": "object",
            "properties": {
                "chain": {"type": "string"},
                "cluster": {"type": "string"},
            },
            "required": ["chain", "cluster"],
            "additionalProperties": False,
        },
    ),
    "rag_search": ToolSpec(
        "rag_search",
        "Retrieve cited sources from the RMI knowledge base.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
}


def select_tools(alias: AliasSpec, requested: list[str]) -> list[ToolSpec]:
    """Grant alias tools; client hints may narrow, never widen, the set."""
    granted = [name for name in alias.tools if name in TOOL_SPECS]
    if requested:
        hints = set(requested)
        narrowed = [name for name in granted if name in hints]
        if narrowed:
            return [TOOL_SPECS[name] for name in narrowed]
    return [TOOL_SPECS[name] for name in granted]


def is_allowed(alias: AliasSpec, name: str) -> bool:
    """True only when the alias grants the tool and the tool exists."""
    return name in alias.tools and name in TOOL_SPECS


class ToolExecutor(Protocol):
    """Execution seam — tests inject fakes, production uses the internal API."""

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...
