"""Transaction tracing — receipt logs, 4byte decoding, wallet-hop graphs.

Public surface for the tracing pipeline:

    from app.domains.scanner.tracing import decode_transaction

Powered by:
  - receipt.py   — RPC router receipt extraction + Etherscan/Alchemy fallbacks
  - fourbyte.py  — 4byte directory signature resolution (Redis-cached)
  - hops.py      — wallet→wallet hop extraction from decoded logs
  - graph.py     — cluster graph builder for RugMaps
"""

from __future__ import annotations

from app.domains.scanner.tracing.models import (
    DecodedLog,
    DecodeResponse,
    GraphEdge,
    GraphNode,
    TraceGraph,
    WalletHop,
)
from app.domains.scanner.tracing.service import (
    build_cluster_graph,
    build_wallet_hops,
    decode_transaction,
    fetch_tx_logs,
    is_supported_chain,
)

__all__ = [
    "DecodeResponse",
    "DecodedLog",
    "GraphEdge",
    "GraphNode",
    "TraceGraph",
    "WalletHop",
    "build_cluster_graph",
    "build_wallet_hops",
    "decode_transaction",
    "fetch_tx_logs",
    "is_supported_chain",
]
