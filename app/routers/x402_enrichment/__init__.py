"""
x402 Tool Response Enrichment Middleware
═══════════════════════════════════════════════════════════

Post-execution enrichment layer that annotates x402 tool responses
with wallet intelligence from the Memory Bank (393K labels, 155K addresses)
and live Etherscan V2 contract name lookups.

Architecture:
  1. Tool executes → raw result
  2. Extract addresses from result
  3. Batch ClickHouse lookup (label_name, label_category, is_sanctioned)
  4. Live Etherscan V2 contract name lookups for unknown addresses
  5. If high-value: RAG similarity search against known scam patterns
  6. Optionally: LLM-synthesize a human-readable enrichment paragraph
  7. Merge enrichment into response JSON

Caching: Redis x402:enrich:{addr} with 1hr TTL.
Opt-out: ?enrich=false in query params.
Always opt-in for premium tools, opt-out for speed-only callers.

Storage impact: ~500 Redis keys at steady state, 0 ClickHouse writes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.routers.x402_enrichment.labels import (
    build_risk_summary,
    extract_addresses,
    lookup_labels,
    search_similar_patterns,
)
from app.routers.x402_enrichment.models import CHAIN_SUFFIXES, ENRICHABLE_CATEGORIES, EnrichmentResult

logger = get_logger("x402.enrich")


def _is_enrichable(tool_name: str) -> str:
    """Check if tool is enrichable, handling per-chain variants."""
    if "_" in tool_name:
        parts = tool_name.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in CHAIN_SUFFIXES:
            base_name = parts[0]
            for cat, tools in ENRICHABLE_CATEGORIES.items():
                if base_name in tools:
                    return cat
    for cat, tools in ENRICHABLE_CATEGORIES.items():
        if tool_name in tools:
            return cat
    return ""


def enrich_tool_response(
    tool_name: str,
    raw_result: dict[str, Any],
    request_params: dict[str, Any] | None = None,
    opt_out: bool = False,
) -> dict[str, Any]:
    """
    Main entry point: enrich an x402 tool response with wallet intelligence.

    Called after tool execution, before the response is sent to the client.
    Returns the enriched result dict (mutates in place, also returns for chaining).
    """
    if opt_out:
        return raw_result

    try:
        r = _redis()
    except (ConnectionError, TimeoutError, OSError):
        r = None

    # Determine enrichment type
    category = _is_enrichable(tool_name)

    if not category:
        return raw_result  # Not an enrichable tool

    # 1. Extract addresses
    addresses = extract_addresses(raw_result, tool_name)
    if not addresses:
        return raw_result

    # 2. Batch ClickHouse label lookup
    labels, ch_ms = lookup_labels(addresses)

    # 3. Identify sanctioned wallets
    sanctioned = [a for a, lbls in labels.items() if any(line.is_sanctioned for line in lbls)]

    # 4. RAG similarity search (only for wallet-centric tools)
    similar_patterns = []
    rag_ms = 0.0
    if category in ("wallet", "security"):
        similar_patterns, rag_ms = asyncio.run(search_similar_patterns(addresses[:10], r))

    # 5. Build enrichment result
    enrichment = EnrichmentResult(
        labels=labels,
        sanctioned=sanctioned,
        risk_summary=build_risk_summary(labels, sanctioned, similar_patterns),
        similar_patterns=similar_patterns,
        lookup_ms=ch_ms,
        rag_ms=rag_ms,
    )

    # 5b. Scam pattern detection (wired from app.rag.compat)
    scam_flags = []
    try:
        from app.rag.compat import detect_scam_patterns

        for addr in addresses[:5]:
            try:
                result = asyncio.run(detect_scam_patterns({"address": addr, "chain": "ethereum"}, 0.65))
                if result and result.get("risk_score", 0) > 0:
                    scam_flags.append(
                        {
                            "address": addr[:10] + "...",
                            "risk_score": result.get("risk_score", 0),
                            "patterns": result.get("patterns", [])[:3],
                        }
                    )
            except (RuntimeError, ConnectionError, TimeoutError) as e:
                logger.warning("Enrichment init error: %s", e)
    except (ImportError, RuntimeError) as e:
        logger.warning("Enrichment import error: %s", e)

    # 5c. LLM-synthesized summary (DeepSeek Flash, ~$0.0003/call)
    llm_summary = ""
    if enrichment.risk_summary and scam_flags:
        from app.routers.x402_enrichment.utils import _synthesize_enrichment

        llm_summary = _synthesize_enrichment(enrichment.risk_summary, scam_flags, tool_name)

    # 5d. Cron intel injection: recent RMI intelligence briefings

    intel_context = _load_recent_intel(r)
    if intel_context:
        raw_result["_intel"] = intel_context

    # 5e. Confidence scoring: quantify data quality for every response
    try:
        from app.routers.x402_advanced_tools import compute_confidence

        raw_result["_confidence"] = compute_confidence(raw_result)
    except (ImportError, TypeError, ValueError) as e:
        logger.warning("Confidence scoring import failed: %s", e)

    # 6. Merge into response
    sanction_count = len(sanctioned)
    raw_result["_enrichment"] = {
        "source": "wallet_memory_bank",
        "labels_found": sum(1 for v in labels.values() if v),
        "addresses_checked": len(addresses),
        "sanctioned": sanction_count if sanction_count > 0 else None,
        "risk_summary": enrichment.risk_summary or None,
        "llm_summary": llm_summary or None,
        "scam_flags": scam_flags if scam_flags else None,
        "label_details": (
            {
                a: [
                    {
                        "category": line.label_category,
                        "name": line.label_name,
                        "source": line.source,
                        "sanctioned": line.is_sanctioned,
                    }
                    for line in lbls
                ]
                for a, lbls in labels.items()
                if lbls
            }
            if any(v for v in labels.values())
            else None
        ),
        "similar_patterns": similar_patterns if similar_patterns else None,
        "performance_ms": round(enrichment.lookup_ms + enrichment.rag_ms, 1),
    }

    # Clean up None values
    enrich = raw_result["_enrichment"]
    for k in list(enrich.keys()):
        if (
            enrich[k] is None
            or (isinstance(enrich[k], dict) and not enrich[k])
            or (isinstance(enrich[k], list) and not enrich[k])
        ):
            del enrich[k]

    if not enrich.get("label_details") and not enrich.get("risk_summary"):
        enrich["note"] = "No wallet labels found for queried addresses"

    # 7. Alert hooks for high-risk findings
    if sanction_count > 0:
        from app.routers.x402_enrichment.utils import _fire_enrichment_alert

        _fire_enrichment_alert(tool_name, "sanctioned", sanctioned, r)
    if scam_flags:
        from app.routers.x402_enrichment.utils import _fire_enrichment_alert

        _fire_enrichment_alert(tool_name, "scam_pattern", [s["address"] for s in scam_flags], r)

    # 8. Track addresses for RAG discovery pipeline
    if addresses:
        try:
            for addr in addresses[:5]:
                r.lpush(
                    "x402:recent:addresses",
                    json.dumps(
                        {
                            "address": addr,
                            "tool": tool_name,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    ),
                )
            r.ltrim("x402:recent:addresses", 0, 999)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error(f"enrich_tool_response failed: {e}")

    return raw_result


# ── Re-exports for legacy import compatibility ─────────────────────

from app.routers.x402_enrichment.models import (  # noqa: E402 — module docstring precedes import block
    ENRICH_CACHE_TTL,
    WalletLabel,
)
from app.routers.x402_enrichment.utils import (  # noqa: E402 — module docstring precedes import block
    _fire_enrichment_alert,
    _load_recent_intel,
    _redis,
    _synthesize_enrichment,
    get_cached_enrichment,
    lookup_contract_names,
    preload_enrichment_cache,
    propagate_labels_cross_chain,
)

__all__ = [
    "ENRICH_CACHE_TTL",
    "WalletLabel",
    "_fire_enrichment_alert",
    "_load_recent_intel",
    "_redis",
    "_synthesize_enrichment",
    "build_risk_summary",
    "enrich_tool_response",
    "extract_addresses",
    "get_cached_enrichment",
    "lookup_contract_names",
    "lookup_labels",
    "preload_enrichment_cache",
    "propagate_labels_cross_chain",
    "search_similar_patterns",
]
