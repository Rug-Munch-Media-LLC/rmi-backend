"""
Shared deployer auto-labeler logic.

Consolidated from two identical implementations:
  - app/domains/scanner/service/scan.py:252-338
  - app/domains/scanner/unified_token_scanner.py:509-617

Usage:
    from app.domains.scanner.shared.deployer_labeler import auto_label_deployer

    await auto_label_deployer(
        deployer_address="0x...",
        chain="ethereum",
        risk_score=85.0,
        risk_label="CRITICAL",
        token_address="0x...",
        token_name="MyToken",
        token_symbol="MTK",
        safety_score=25.0,
        risk_flags=["honeypot"],
        source="unified_scanner",
    )
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


async def auto_label_deployer(
    *,
    deployer_address: str,
    chain: str,
    risk_score: float,
    risk_label: str,
    token_address: str,
    token_name: str = "",
    token_symbol: str = "",
    safety_score: float = 50.0,
    risk_flags: list[str] | None = None,
    source: str = "scanner",
) -> None:
    """Auto-label a high-risk deployer wallet.

    Called after a token scan when the deployer's risk score exceeds
    the threshold.  Wires deployer data into:
      a) RAG wallet profile (ingest_wallet_profile)
      b) ClickHouse scam_addresses + deployer_history tables
      c) AutoLabeler behavioral observation system

    All errors are caught and logged --- this is a best-effort background
    operation that must never propagate exceptions to the caller.
    """
    if not deployer_address or deployer_address == "unknown":
        return

    risk_flags = risk_flags or []
    labels = ["high_risk_deployer"] if risk_score < 85 else ["malicious_deployer"]

    # (a) Ingest wallet profile to RAG
    try:
        from app.rag.ingestion import ingest_wallet_profile as _ingest_wallet

        await _ingest_wallet(
            wallet_address=deployer_address,
            chain=chain,
            labels=labels,
            risk_score=risk_score,
            entities=[f"deployer_of:{token_address[:16]}"],
            extra={
                "token_name": token_name,
                "token_symbol": token_symbol,
                "safety_score": safety_score,
                "source": source,
                "risk_flags": risk_flags[:10],
            },
        )
    except ImportError:
        logger.debug("wallet_rag_ingest_skipped")
    except Exception as exc:
        logger.warning("wallet_rag_ingest_failed", error=str(exc)[:100])

    # (b) Persist to ClickHouse
    try:
        from app.wallet_memory.storage import get_storage

        storage = get_storage()
        tasks = await asyncio.gather(
            storage.save_scam_address(
                address=deployer_address,
                chain_id=chain,
                source=source,
                threat_type=risk_label.lower(),
                confidence=min(1.0, risk_score / 100.0),
                evidence=(f"Scanned token {token_address[:16]} risk={risk_label} flags={','.join(risk_flags[:5])}"),
            ),
            storage.save_deployer_event(
                deployer=deployer_address,
                chain_id=chain,
                token_address=token_address,
                token_name=token_name,
                token_symbol=token_symbol,
                deployed_at=None,
                outcome="scam" if risk_label == "CRITICAL" else "suspicious",
                is_scam=(risk_label == "CRITICAL"),
                risk_score=risk_score,
            ),
            return_exceptions=True,
        )
        _log_storage_errors(tasks)
    except ImportError:
        logger.debug("wallet_storage_persist_skipped")
    except Exception as exc:
        logger.warning("wallet_storage_persist_failed", error=str(exc)[:100])

    # (c) Feed observation to AutoLabeler
    try:
        from app.auto_labeler import get_auto_labeler

        labeler = get_auto_labeler()
        await labeler.observe_wallet(
            address=deployer_address,
            chain=chain,
            observations={
                "event": "token_deployed",
                "token_address": token_address,
                "risk_score": risk_score,
                "reputation_label": risk_label,
            },
        )
    except ImportError:
        logger.debug("auto_labeler_observe_skipped")
    except Exception as exc:
        logger.warning("auto_labeler_observe_failed", error=str(exc)[:100])

    logger.info(
        "deployer_auto_labeled",
        deployer=deployer_address[:16],
        chain=chain,
        labels=labels,
        risk=risk_label,
    )


def _log_storage_errors(tasks: list[Any]) -> None:
    """Log any exceptions from asyncio.gather storage tasks."""
    for i, t in enumerate(tasks):
        if isinstance(t, Exception):
            logger.warning("wallet_storage_persist_subtask_failed", index=i, error=str(t)[:100])
