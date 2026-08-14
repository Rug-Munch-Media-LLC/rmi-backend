"""Trace executor for the chat tool-use layer.

Wallet/entity forensics: resolves a cross-chain entity, else falls back to an
entity-graph node/edge summary. Fail-open: returns ``None`` on any
error/timeout and NEVER raises.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Per-call timeout for the trace resolver / graph fallback (the dispatcher's
# outer 8s cap still applies when routed through ``_run_tool_for_intent``).
_TRACE_TIMEOUT_SECONDS = 10.0


def _render_trace_summary(result: dict[str, Any]) -> str | None:
    """Compact forensics summary from a cross-chain entity resolution."""
    if not isinstance(result, dict):
        return None
    links = result.get("addresses")
    if not isinstance(links, list):
        links = []
    short = str(result.get("address") or "?")[:14]
    entity = result.get("entity_type") or "unknown"
    risk = result.get("risk") or "unknown"

    parts = [f"entity {entity}, risk {risk}"]
    deployer_chains = sorted(
        {
            str(link.get("chain", ""))
            for link in links
            if isinstance(link, dict) and link.get("evidence_type") == "same_deployer"
        }
    )
    funder_links = [link for link in links if isinstance(link, dict) and link.get("evidence_type") == "same_funder"]
    if deployer_chains:
        parts.append("deployer on " + ", ".join(deployer_chains))
    if funder_links:
        details = funder_links[0].get("details")
        funder = str(details.get("funder") or "?") if isinstance(details, dict) else "?"
        cand_chains = sorted({str(link.get("chain", "")) for link in funder_links})
        cand_addrs = sorted({str(link.get("address", ""))[:10] for link in funder_links})
        funder_short = funder[:10] + "…" if len(funder) > 10 else funder
        parts.append(
            f"first funder {funder_short}; same-funder candidates on {', '.join(cand_chains)} ({', '.join(cand_addrs)})"
        )
    if links:
        all_chains = ", ".join(sorted({str(link.get("chain", "")) for link in links if isinstance(link, dict)}))
        parts.append(f"{len(links)} cross-chain link(s): {all_chains}")
    else:
        parts.append("no cross-chain links found")
    return f"Trace {short}…: " + "; ".join(parts) + "."


def _render_graph_summary(graph: dict[str, Any]) -> str | None:
    """Compact fallback summary from an entity graph build (node/edge counts)."""
    if not isinstance(graph, dict):
        return None
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    edges = graph.get("edges")
    if not isinstance(edges, list):
        edges = []
    stats = graph.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    center = graph.get("center")
    addr = str(center.get("address", ""))[:14] if isinstance(center, dict) else ""

    parts = [f"graph: {len(nodes)} node(s), {len(edges)} edge(s)"]
    entity_types = stats.get("entity_types")
    if isinstance(entity_types, list) and entity_types:
        parts.append("types: " + ", ".join(str(t) for t in entity_types[:5]))
    risk_dist = stats.get("risk_distribution")
    if isinstance(risk_dist, dict) and risk_dist.get("high"):
        parts.append(f"{risk_dist['high']} high-risk node(s)")
    prefix = f"Trace {addr}…: " if addr else "Trace: "
    return prefix + "; ".join(parts) + "."


async def _exec_trace(message: str) -> str | None:
    """Wallet/entity forensics: resolve cross-chain entity, else graph summary.

    Calls ``resolve_cross_chain_entity`` directly (no x402 endpoints) with a
    per-call timeout. When resolution is unavailable or empty, falls back to
    ``build_entity_graph`` for a node/edge summary. Fail-open → None.
    """
    # Patch-sensitive shared helpers resolve through the package shim at call
    # time so tests can mock them on app.domains.chat_tools.* (lazy to avoid
    # an import cycle — the shim imports this module).
    from app.domains.chat_tools import _chain_for_address, _extract_first_address

    address = _extract_first_address(message)
    if not address:
        return None
    chain = _chain_for_address(address)
    resolved: dict[str, Any] | None = None
    try:
        from app.domains.scanner.engines.cross_chain_entity import resolve_cross_chain_entity

        resolved = await asyncio.wait_for(
            resolve_cross_chain_entity(address, chain),
            timeout=_TRACE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug("chat_tool_trace_resolve_failed", error=str(e)[:120])

    if isinstance(resolved, dict) and resolved.get("result") == "scanned":
        summary = _render_trace_summary(resolved)
        if summary:
            return summary

    # Fallback: entity graph node/edge summary when resolution is unavailable.
    try:
        from app.domains.scanner.entity_graph import build_entity_graph

        graph = await asyncio.wait_for(
            asyncio.to_thread(build_entity_graph, address, chain),
            timeout=_TRACE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug("chat_tool_trace_graph_failed", error=str(e)[:120])
        return None
    return _render_graph_summary(graph)
