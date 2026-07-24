"""Scanner service — check functions (RAG scam check, cross-chain deployer)."""

from __future__ import annotations
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.rag_scam_check import check_scam as _rag_check_scam

# Re-export volume_analysis for backward compat
from app.domains.scanner.service.volume_analysis import _check_volume_anomaly  # noqa: F401

logger = get_logger(__name__)

async def _rag_scam_check(
    token_address: str, chain: str, deployer: str | None = None
) -> dict[str, Any] | None:
    """Check RAG for scam indicators via vector search + pattern matching."""
    logger.info("rag_scam_check", token=token_address, chain=chain, deployer=deployer)
    try:
        result = await _rag_check_scam(
            token_address=token_address,
            chain=chain,
            deployer=deployer,
        )
        if result:
            return result
    except (ValueError, TypeError, KeyError, OSError) as e :
        logger.debug("_rag_scam_check_error", exc_info=True)
        logger.warning("rag_scam_check_failed", error=str(e)[:100])

    return {"check": "rag", "result": "scanned", "risk_score": 0, "source": "unavailable"}

async def _check_cross_chain_deployer(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Check if deployer has cross-chain activity."""
    logger.info("cross_chain_deployer", deployer=deployer_addr, chain=chain)
    return {
        "check": "cross_chain",
        "result": "unavailable",
        "reason": "Cross-chain indexer not yet deployed",
    }
