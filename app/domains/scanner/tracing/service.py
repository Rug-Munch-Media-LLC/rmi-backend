"""Tracing service — orchestrates the full decode pipeline.

Pipeline:
  1. fetch_tx_logs(tx_hash, chain)      — receipt logs via RPC router + fallbacks
  2. resolve each log's topic0 via 4byte — human-readable event signature
  3. decode args (indexed topics + ABI data)
  4. sort by logIndex (enforced)
  5. build wallet→wallet hops
  6. build cluster graph for RugMaps

Optional enhancement: debug_traceTransaction internal-call hops are merged
when ``include_trace=True`` (best-effort; only paid endpoints support it).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.tracing.fourbyte import decode_log, resolve_event_signature
from app.domains.scanner.tracing.graph import build_cluster_graph
from app.domains.scanner.tracing.hops import build_wallet_hops
from app.domains.scanner.tracing.models import DecodedLog, DecodeResponse
from app.domains.scanner.tracing.receipt import (
    extract_trace_hops,
    fetch_debug_trace,
    fetch_tx_logs,
    is_supported_chain,
)

logger = get_logger(__name__)

TX_HASH_PATTERN = "0x" + "0" * 64  # length check helper


def _validate_tx_hash(tx_hash: str) -> str:
    """Validate a 0x-prefixed 64-hex-char tx hash."""
    if not isinstance(tx_hash, str) or not tx_hash:
        raise ValidationError("tx_hash is required")
    tx_hash = tx_hash.strip()
    if not tx_hash.startswith("0x"):
        raise ValidationError("tx_hash must be 0x-prefixed")
    if len(tx_hash) != 66:
        raise ValidationError("tx_hash must be 64 hex characters (66 with 0x prefix)")
    try:
        bytes.fromhex(tx_hash[2:])
    except ValueError as e:
        raise ValidationError("tx_hash contains non-hex characters") from e
    return tx_hash.lower()


async def _decode_receipt_logs(logs: list[dict[str, Any]]) -> list[DecodedLog]:
    """Decode each receipt log: 4byte signature + args. Sorted by logIndex."""
    decoded: list[DecodedLog] = []
    for log in logs:
        try:
            log_index = int(log.get("logIndex", "0x0"), 16)
        except (ValueError, TypeError):
            log_index = 0
        topics = log.get("topics") or []
        topic0 = topics[0] if topics else ""
        signature = await resolve_event_signature(topic0) if topic0 else None
        event_name, full_sig, args = decode_log(log, signature)
        decoded.append(
            DecodedLog(
                log_index=log_index,
                address=str(log.get("address", "")).lower(),
                topics=topics,
                data=log.get("data") or "0x",
                decoded_event=full_sig,
                event_name=event_name,
                args=args,
            )
        )
    # Enforce logIndex sort (receipts are already ordered, but be explicit).
    decoded.sort(key=lambda d: d.log_index)
    return decoded


async def decode_transaction(
    tx_hash: str,
    chain: str,
    include_trace: bool = False,
) -> DecodeResponse:
    """Full tracing pipeline for a single transaction.

    Args:
        tx_hash: 0x-prefixed 64-char transaction hash.
        chain: EVM chain name (ethereum, base, polygon, arbitrum, ...).
        include_trace: optionally attempt debug_traceTransaction internal
            calls (best-effort, paid endpoints only).

    Returns the DecodeResponse contract for the tracing route.
    """
    tx_hash = _validate_tx_hash(tx_hash)
    if not chain or not chain.strip():
        raise ValidationError("chain is required")
    chain = chain.strip().lower()
    if not is_supported_chain(chain):
        raise ValidationError(
            f"chain '{chain}' is not supported by the tracing pipeline; "
            "supported: ethereum, bsc, polygon, arbitrum, base, optimism, "
            "avalanche, fantom, linea, zksync, scroll, mantle, gnosis"
        )

    logs, source = await fetch_tx_logs(tx_hash, chain)
    decoded_logs = await _decode_receipt_logs(logs)

    # decoded map for hop extraction: log_index → {"event", "args"}
    decoded_map: dict[str, dict[str, Any]] = {}
    for d in decoded_logs:
        decoded_map[str(d.log_index)] = {"event": d.event_name or "", "args": d.args}

    hops = build_wallet_hops(logs, decoded_map)

    trace_hops: list[Any] | None = None
    if include_trace:
        trace = await fetch_debug_trace(tx_hash, chain)
        if trace:
            trace_hops = extract_trace_hops(trace)

    graph = build_cluster_graph(hops, wallet_hops_extra=trace_hops)

    return DecodeResponse(
        tx_hash=tx_hash,
        chain=chain,
        logs=decoded_logs,
        wallet_hops=hops,
        graph=graph,
        trace=trace_hops,
        source=source,
    )


__all__ = [
    "DecodeResponse",
    "build_cluster_graph",
    "build_wallet_hops",
    "decode_transaction",
    "fetch_tx_logs",
    "is_supported_chain",
]
