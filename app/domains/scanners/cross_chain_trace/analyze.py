"""
Cross-Chain Trace — Compatibility Wrappers
===========================================
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from .analyzer import CrossChainTraceAnalyzer
from .utils import _report_to_dict

logger = get_logger("cross_chain_trace")

_cross_chain_trace_analyzer: CrossChainTraceAnalyzer | None = None


def get_analyzer() -> CrossChainTraceAnalyzer:
    """Get the shared CrossChainTraceAnalyzer singleton."""
    global _cross_chain_trace_analyzer
    if _cross_chain_trace_analyzer is None:
        _cross_chain_trace_analyzer = CrossChainTraceAnalyzer()
    return _cross_chain_trace_analyzer


async def analyze_wallet(wallet: str, chain: str = "ethereum") -> dict[str, Any]:
    """Standalone entry point: trace a wallet address across chains."""
    analyzer = get_analyzer()
    report = await analyzer.analyze(wallet, chain)
    return _report_to_dict(report)


async def analyze(token_address: str, chain: str = "solana", **kwargs: Any) -> dict[str, Any]:
    """Sentinel pipeline-compatible entry point.

    For token-centric scans, trace the token contract's deployer
    (if available) or fall back to tracing the token address itself.
    """
    deployer = await _resolve_token_deployer(token_address, chain)
    if deployer:
        logger.info("CrossChainTrace: tracing deployer %s for token %s", deployer, token_address)
        return await analyze_wallet(deployer, chain)

    logger.info("CrossChainTrace: tracing token address %s as wallet", token_address)
    return await analyze_wallet(token_address, chain)


async def _resolve_token_deployer(token_address: str, chain: str) -> str | None:
    """Find the deployer address for a token via chain-indexer deployer_events."""
    try:
        from app.domains.databus.providers.chains.chain_indexer import (
            _chain_indexer_deployer_history,
        )

        result = await _chain_indexer_deployer_history(
            address=token_address,
            chain=chain,
            limit=1,
        )
        if result and result.get("data", {}).get("deployer_events"):
            events = result["data"]["deployer_events"]
            if events:
                return events[0].get("deployer")
    except (ImportError, ValueError, RuntimeError, OSError) as exc:
        logger.debug("deployer lookup failed: %s", exc)
    return None
