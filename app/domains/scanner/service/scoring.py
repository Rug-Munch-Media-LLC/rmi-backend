"""Scanner service — risk scoring engine + RAG ingestion hook."""

from __future__ import annotations
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future similarity searches.

    Uses the new ``app.rag.ingestion.ingest_scan_result`` pipeline.
    Falls back gracefully if the RAG system is unavailable.
    """
    try:
        from app.rag.ingestion import ingest_scan_result as _ingest

        report = await _ingest(
            token_address=scan_result.token_address,
            chain=getattr(scan_result, "chain", "solana"),
            safety_score=float(getattr(scan_result, "safety_score", 50)),
            risk_flags=list(getattr(scan_result, "risk_flags", [])),
            symbol=getattr(scan_result, "symbol", ""),
            name=getattr(scan_result, "name", ""),
            extra={
                "confidence": getattr(scan_result, "confidence", 0),
                "tier_required": getattr(scan_result, "tier_required", "free"),
            },
        )
        logger.info(
            "ingest_to_rag",
            token=scan_result.token_address,
            status=report.status,
            chunks=report.chunks_total,
        )
    except ImportError:
        logger.debug("ingest_to_rag_skipped (RAG ingestion module not available)")
    except Exception as exc:
        logger.warning("ingest_to_rag_failed", token=scan_result.token_address[:16], error=str(exc))

def _calculate_safety_score(modules_results: list[dict[str, Any]]) -> int:
    """Calculate safety score (0-100) from all module results.

    Baseline: 50 (unknown/neutral).
    Each module contributes risk based on its findings:
    - Modules returning error/offline: no impact
    - Clean modules: +5 to score
    - Risk flags: -risk_score contribution
    """
    score = 50  # Neutral baseline
    clean_bonus = 0
    risk_penalty = 0
    modules_with_data = 0

    for mod_entry in modules_results:
        r = mod_entry.get("result", {})
        if not isinstance(r, dict):
            continue
        result_status = r.get("result", "")
        if result_status in ("error", "pending", "unavailable"):
            continue  # Module didn't return useful data

        modules_with_data += 1

        # Check for risk indicators
        risk_score = r.get("risk_score", 0)
        if risk_score and isinstance(risk_score, (int, float)):
            risk_penalty += risk_score * 0.4  # penalty proportional to risk

        # ── Core risk flags ──
        is_honeypot = r.get("is_honeypot", False)
        if is_honeypot:
            risk_penalty += 35

        is_spam = r.get("is_spam", False)
        if is_spam:
            risk_penalty += 30

        is_sanctioned = r.get("is_sanctioned", False)
        if is_sanctioned:
            risk_penalty += 50

        is_bundled = r.get("is_bundled", False)
        if is_bundled:
            confidence = r.get("confidence", 0)
            risk_penalty += confidence * 50

        is_verified = r.get("is_verified", None)
        if is_verified is True:
            clean_bonus += 10
        elif is_verified is False:
            risk_penalty += 10

        # ── Deployer reputation ──
        reputation_label = r.get("reputation_label", "")
        if reputation_label == "CRITICAL":
            risk_penalty += 30
        elif reputation_label == "HIGH":
            risk_penalty += 20
        elif reputation_label == "LOW":
            clean_bonus += 10

        # ── Copycat ──
        copycat_result = r.get("result", "")
        if copycat_result == "high_risk":
            risk_penalty += 25
        elif copycat_result == "medium_risk":
            risk_penalty += 15
        elif copycat_result == "clean":
            clean_bonus += 5

        # ── LP locked ──
        lp_locked = r.get("liquidity_locked_pct", 0)
        if lp_locked and isinstance(lp_locked, (int, float)):
            if lp_locked >= 90:
                clean_bonus += 10
            elif lp_locked <= 10 and r.get("liquidity_usd", 0) > 0:
                risk_penalty += 20

        # ── Ownership renounced (new check) ──
        ownership_renounced = r.get("ownership_renounced", None)
        if ownership_renounced is True:
            clean_bonus += 10
        elif ownership_renounced is False:
            # Active ownership is moderate risk
            risk_level = r.get("risk_level", "")
            if risk_level == "active_proxy_owner":
                risk_penalty += 25
            else:
                risk_penalty += 10

        # ── High concentration (holders) ──
        concentration = r.get("concentration_pct", 0)
        if isinstance(concentration, (int, float)) and concentration > 50:
            risk_penalty += 15

        # ── LP concentration (new check) ──
        top10_pct = r.get("top10_pct", 0)
        if isinstance(top10_pct, (int, float)) and top10_pct > 0:
            if top10_pct >= 90:
                risk_penalty += 30
            elif top10_pct >= 70:
                risk_penalty += 20
            elif top10_pct >= 50:
                risk_penalty += 10

        # ── Social sentiment (new check) ──
        if r.get("is_suspicious"):
            suspicion_count = len(r.get("suspicion_reasons", []))
            risk_penalty += 15 * suspicion_count

        # ── Token age (new check) ──
        age_risk_level = r.get("risk_level", "")
        if age_risk_level == "critical":
            risk_penalty += 20
        elif age_risk_level == "high":
            risk_penalty += 12
        elif age_risk_level == "medium":
            risk_penalty += 5
        elif age_risk_level == "low":
            clean_bonus += 5

        # ── Volume/liquidity ratio (new check) ──
        if r.get("is_wash_trading_suspicious"):
            risk_penalty += 20
        vol_risk_level = r.get("risk_level", "")
        if vol_risk_level == "critical":
            risk_penalty += 25
        elif vol_risk_level == "high":
            risk_penalty += 15

    # Apply modifiers
    score = score + clean_bonus - risk_penalty

    # If we have good data, boost confidence can raise score
    if modules_with_data >= 6 and risk_penalty == 0:
        score = min(95, score + 10)

    # Clamp to 0-100
    return max(0, min(100, int(score)))
