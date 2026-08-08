"""Tracing domain models — decoded logs, wallet hops, and cluster graphs.

Pure Pydantic models. No I/O, no external deps beyond pydantic.
The wire contract for POST /api/v1/tracing/decode is:

    {
      "tx_hash": "0x...",
      "chain": "ethereum",
      "logs": [
        {
          "logIndex": 0,
          "address": "0x...",
          "topics": ["0x..."],
          "data": "0x...",
          "decoded_event": "Transfer(address indexed from, ...)",
          "event_name": "Transfer",
          "args": {"from": "0x...", "to": "0x...", "value": 1000000}
        }
      ],
      "wallet_hops": [
        {"from": "0x...", "to": "0x...", "value": "1000000", "token": "0x..."}
      ],
      "graph": {"nodes": [...], "edges": [...]}
    }

``from`` is a Python keyword, so the field is named ``from_`` with a
serialization alias of "from" — FastAPI renders it as ``from`` in JSON.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DecodeResponse",
    "DecodedEvent",
    "DecodedLog",
    "GraphEdge",
    "GraphNode",
    "TraceGraph",
    "WalletHop",
]


class DecodedEvent(BaseModel):
    """A human-readable event signature resolved from topic0 via 4byte."""

    signature: str  # e.g. "Transfer(address indexed from, address indexed to, uint256 value)"
    name: str  # e.g. "Transfer"
    inputs: list[dict[str, Any]] = Field(default_factory=list)


class DecodedLog(BaseModel):
    """A single receipt log with its decoded arguments."""

    model_config = ConfigDict(populate_by_name=True)

    log_index: int = Field(serialization_alias="logIndex")
    address: str  # emitting contract
    topics: list[str] = Field(default_factory=list)
    data: str = "0x"
    decoded_event: str | None = None  # full human-readable signature
    event_name: str | None = None  # short name e.g. "Transfer"
    args: dict[str, Any] = Field(default_factory=dict)  # decoded named args


class WalletHop(BaseModel):
    """A wallet → wallet interaction extracted from a decoded log."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(serialization_alias="from")
    to: str
    value: str = "0"  # decimal string (avoids JS float precision loss)
    token: str = ""  # token/contract address involved
    event: str = ""  # event name that produced this hop
    log_index: int | None = None


class GraphNode(BaseModel):
    """A node in the RugMaps cluster graph."""

    id: str
    address: str
    type: str = "wallet"  # wallet | contract | token
    label: str = ""


class GraphEdge(BaseModel):
    """An edge in the RugMaps cluster graph."""

    source: str
    target: str
    value: str = "0"
    token: str = ""
    event: str = ""


class TraceGraph(BaseModel):
    """Cluster graph for RugMaps visualization."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class DecodeResponse(BaseModel):
    """Full response contract for POST /api/v1/tracing/decode."""

    tx_hash: str
    chain: str
    logs: list[DecodedLog] = Field(default_factory=list)
    wallet_hops: list[WalletHop] = Field(default_factory=list)
    graph: TraceGraph = Field(default_factory=TraceGraph)
    trace: list[WalletHop] | None = None  # debug_traceTransaction enhancement (optional)
    source: str = "rpc"  # which provider answered: rpc | etherscan | alchemy | none
