"""Scanner service — risk scoring engine + RAG ingestion hook."""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

# Redis channel/list used by rmi-rag's scanner subscriber + poller.
# Must match ``app.domains.scanner.unified.persistence.publish_rag_finding``.
RAG_INGEST_CHANNEL = "rmi:rag:ingest"
RAG_FINDINGS_QUEUE = "rmi:rag:findings_queue"

# Security-critical modules. When one of these fails (error/unavailable),
# unknown posture is treated as risk rather than absence of risk: a token
# whose GoPlus/honeypot vetting is down must not silently score as safe.
CRITICAL_MODULES: frozenset[str] = frozenset({"goplus", "honeypot"})
DEFAULT_RISK_PENALTY = 15  # safety points subtracted per failed critical module


def _risk_level_from_safety(safety_score: float) -> str | None:
    """Map a safety score (0-100, higher = safer) to a risk level.

    Granular thresholds for best-in-class risk communication:
    - >= 80: Safe (green) — no action needed
    - 60-79: Low risk (light green) — generally safe
    - 40-59: Medium risk (yellow) — exercise caution
    - 20-39: High risk (orange) — significant danger
    - < 20: Critical risk (red) — likely scam

    Returns ``None`` for safe/low so only medium+ findings are forwarded
    to the threat-intel RAG pipeline.
    """
    if safety_score >= 80:
        return None  # Safe — no threat intel needed
    if safety_score >= 60:
        return None  # Low risk — no threat intel needed
    if safety_score >= 40:
        return "medium"
    if safety_score >= 20:
        return "high"
    return "critical"


async def _publish_scan_to_rag_queue(scan_result: Any) -> None:
    """Publish a high/critical scan finding to the rmi-rag Redis queue.

    rmi-backend's ``UnifiedTokenScanner`` publishes via
    ``publish_rag_finding``; the legacy ``scan_token`` path (used by the
    public scanner API) previously only fed the backend-internal RAG and
    never reached rmi-rag. This publishes the same payload shape to the
    ``rmi:rag:ingest`` pub/sub channel and the ``rmi:rag:findings_queue``
    LPUSH list so rmi-rag's subscriber/poller can embed and index it into
    the Qdrant ``threat_intel`` collection.

    Fail-open: any Redis error is logged and swallowed — the scan result
    is already persisted in Postgres and the internal RAG by callers.
    """
    import json as _json

    from app.core.redis import get_redis_async

    safety = float(getattr(scan_result, "safety_score", 50))
    risk_level = _risk_level_from_safety(safety)
    if risk_level is None:
        return  # only medium/high/critical findings are threat-intel material

    r = get_redis_async()
    if r is None:
        logger.warning("rag_queue_redis_unavailable — skipping RAG finding publish")
        return

    chain = getattr(scan_result, "chain", "solana")
    token_address = getattr(scan_result, "token_address", "unknown")

    # Dedup: don't republish the same token within 24h
    dedup_key = f"rag:dedup:{chain}:{token_address}"
    if await r.exists(dedup_key):
        logger.debug("rag_queue_duplicate_skipped", chain=chain, token=token_address)
        return
    await r.setex(dedup_key, 86400, "1")

    modules_run = []
    for m in getattr(scan_result, "modules_run", []) or []:
        if isinstance(m, dict):
            name = m.get("module") or m.get("name")
            if name:
                modules_run.append(str(name))

    payload = {
        "token_address": token_address,
        "chain": chain,
        "name": getattr(scan_result, "name", ""),
        "symbol": getattr(scan_result, "symbol", ""),
        "safety_score": safety,
        "confidence": float(getattr(scan_result, "confidence", 0) or 0),
        "risk_level": risk_level,
        "risk_flags": list(getattr(scan_result, "risk_flags", []))[:20],
        "green_flags": [],
        "price_usd": None,
        "liquidity_usd": None,
        "age_hours": None,
        "scanned_at": getattr(scan_result, "scanned_at", ""),
        "modules_run": modules_run[:30],
    }
    payload_json = _json.dumps(payload)

    await r.publish(RAG_INGEST_CHANNEL, payload_json)
    await r.lpush(RAG_FINDINGS_QUEUE, payload_json)
    logger.info(
        "rag_queue_published",
        token=getattr(scan_result, "token_address", "unknown"),
        chain=getattr(scan_result, "chain", "solana"),
        risk=risk_level,
    )


async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future similarity searches.

    Uses the new ``app.rag.ingestion.ingest_scan_result`` pipeline.
    Falls back gracefully if the RAG system is unavailable.
    """
    try:
        from app.integrations.rugbrain_client import rugbrain_ingest

        chain = getattr(scan_result, "chain", "solana")
        content = f"Token: {scan_result.token_address} ({getattr(scan_result, 'symbol', '')}) on {chain}. Score: {getattr(scan_result, 'safety_score', 50)}"
        metadata = {
            "token_address": scan_result.token_address,
            "chain": chain,
            "safety_score": getattr(scan_result, "safety_score", 50),
            "risk_flags": list(getattr(scan_result, "risk_flags", [])),
            "symbol": getattr(scan_result, "symbol", ""),
            "name": getattr(scan_result, "name", ""),
            "confidence": getattr(scan_result, "confidence", 0),
            "tier_required": getattr(scan_result, "tier_required", "free"),
        }
        ok = await rugbrain_ingest(content=content, collection="token_scans", metadata=metadata)
        logger.info(
            "ingest_to_rag",
            token=scan_result.token_address,
            status="ok" if ok else "failed",
        )
    except ImportError:
        logger.debug("ingest_to_rag_skipped (RAG ingestion module not available)")
    except Exception as exc:
        logger.warning("ingest_to_rag_failed", token=scan_result.token_address[:16], error=str(exc))

    # Publish high/critical findings to rmi-rag's Redis queue (external RAG).
    try:
        await _publish_scan_to_rag_queue(scan_result)
    except Exception as exc:
        logger.warning("rag_queue_publish_failed", token=scan_result.token_address[:16], error=str(exc))


def _calculate_safety_score(modules_results: list[dict[str, Any]]) -> int:
    """Calculate safety score (0-100) from all module results.

    .. deprecated:: ADR-0001 (docs/adr/ADR-0001-canonical-scoring.md)
        FROZEN for new signals — bug-fixes only. Canonical scorer is
        ``app.domains.scanner.unified.scoring.compute_score``; this module
        is migrated away at Gate C of that ADR. Do NOT add scoring logic
        here.

    Best-in-class methodology:
    - Baseline 35 (unknown/neutral — absence of evidence ≠ evidence of safety)
    - Confidence-weighted modules (reliable sources weighted higher)
    - Compounding severe risks (multiple critical flags compound multiplicatively)
    - Deployer reputation escalation (known rug-pull = automatic critical)
    - Inter-module correlation detection (patterns across modules)
    - Continuous LP lock scoring (not binary thresholds)
    - "Too good to be true" detection (suspicious perfection)
    """
    # ── Module reliability weights (higher = more trustworthy) ──
    MODULE_WEIGHTS = {
        "honeypot": 1.0,  # Direct simulation — highest confidence
        "goplus": 0.9,  # Multi-source aggregation
        "bundle": 0.85,  # On-chain pattern detection
        "copycat": 0.8,  # Bytecode similarity
        "deployer": 0.8,  # Historical behavior
        "liquidity": 0.75,  # On-chain lock verification
        "holders": 0.7,  # Distribution analysis
        "ownership": 0.7,  # On-chain state
        "verification": 0.6,  # Contract verification
        "spam": 0.6,  # Pattern matching
        "token_age": 0.5,  # Temporal signal
        "social_sentiment": 0.4,  # Easily manipulated
        "volume_liquidity_ratio": 0.5,
        "lp_concentration": 0.6,
        "blockscout": 0.5,
        "defiscanner": 0.5,
        "price": 0.3,
        "rag": 0.6,
        "market_data": 0.4,
    }

    score: float = 35  # Baseline: unknown = moderately risky (not neutral)
    clean_bonus: float = 0
    risk_penalty: float = 0
    modules_with_data = 0
    severe_risk_count = 0  # Track compounding severe risks
    risk_categories = set()  # Track inter-module correlation

    for mod_entry in modules_results:
        module_name = str(mod_entry.get("module", ""))
        weight = MODULE_WEIGHTS.get(module_name, 0.5)
        r = mod_entry.get("result", {})
        if not isinstance(r, dict):
            continue
        result_status = r.get("result", "")
        if result_status in ("error", "pending", "unavailable"):
            if module_name in CRITICAL_MODULES and result_status in ("error", "unavailable"):
                risk_penalty += DEFAULT_RISK_PENALTY * weight
                logger.warning(
                    "critical_module_failed_penalty",
                    module=module_name,
                    status=result_status,
                    penalty=DEFAULT_RISK_PENALTY * weight,
                )
            continue

        modules_with_data += 1

        # ── Core risk flags (weighted by module reliability) ──
        is_honeypot = r.get("is_honeypot", False)
        if is_honeypot:
            risk_penalty += 35 * weight
            severe_risk_count += 1
            risk_categories.add("honeypot")

        is_spam = r.get("is_spam", False)
        if is_spam:
            risk_penalty += 30 * weight
            severe_risk_count += 1
            risk_categories.add("spam")

        is_sanctioned = r.get("is_sanctioned", False)
        if is_sanctioned:
            risk_penalty += 50 * weight
            severe_risk_count += 2  # Sanctioned is doubly severe
            risk_categories.add("sanctioned")

        is_bundled = r.get("is_bundled", False)
        if is_bundled:
            confidence = r.get("confidence", 0)
            # Cap bundle penalty at 35 to prevent single-module dominance
            bundle_penalty = min(35, confidence * 35)
            risk_penalty += bundle_penalty * weight
            severe_risk_count += 1
            risk_categories.add("bundle")

        is_verified = r.get("is_verified", None)
        if is_verified is True:
            clean_bonus += 8 * weight
            risk_categories.add("verified")
        elif is_verified is False:
            risk_penalty += 8 * weight

        # ── Deployer reputation (escalated for known bad actors) ──
        reputation_label = r.get("reputation_label", "")
        if reputation_label == "CRITICAL":
            risk_penalty += 40 * weight  # Known rug-pull deployer = severe
            severe_risk_count += 2
            risk_categories.add("deployer_critical")
        elif reputation_label == "HIGH":
            risk_penalty += 25 * weight
            severe_risk_count += 1
            risk_categories.add("deployer_risk")
        elif reputation_label == "LOW":
            clean_bonus += 8 * weight

        # ── Copycat detection ──
        copycat_result = r.get("result", "")
        if copycat_result == "high_risk":
            risk_penalty += 25 * weight
            severe_risk_count += 1
            risk_categories.add("copycat")
        elif copycat_result == "medium_risk":
            risk_penalty += 15 * weight
        elif copycat_result == "clean":
            clean_bonus += 5 * weight

        # ── LP locked (continuous scoring, not binary) ──
        lp_locked = r.get("liquidity_locked_pct", 0)
        if lp_locked and isinstance(lp_locked, (int, float)):
            if lp_locked >= 95:
                clean_bonus += 10 * weight
            elif lp_locked >= 80:
                clean_bonus += 6 * weight
            elif lp_locked >= 50:
                clean_bonus += 2 * weight
            elif lp_locked <= 5 and r.get("liquidity_usd", 0) > 0:
                risk_penalty += 25 * weight  # Very low lock = high risk
                severe_risk_count += 1
            elif lp_locked <= 20 and r.get("liquidity_usd", 0) > 0:
                risk_penalty += 15 * weight

        # ── Ownership renounced ──
        ownership_renounced = r.get("ownership_renounced", None)
        if ownership_renounced is True:
            clean_bonus += 8 * weight
            risk_categories.add("renounced")
        elif ownership_renounced is False:
            risk_level = r.get("risk_level", "")
            if risk_level == "active_proxy_owner":
                risk_penalty += 25 * weight
                severe_risk_count += 1
            else:
                risk_penalty += 10 * weight

        # ── Holder concentration ──
        concentration = r.get("concentration_pct", 0)
        if isinstance(concentration, (int, float)) and concentration > 50:
            risk_penalty += 15 * weight

        # ── Top 10 holder concentration (continuous) ──
        top10_pct = r.get("top10_pct", 0)
        if isinstance(top10_pct, (int, float)) and top10_pct > 0:
            if top10_pct >= 95:
                risk_penalty += 35 * weight
                severe_risk_count += 1
            elif top10_pct >= 80:
                risk_penalty += 25 * weight
            elif top10_pct >= 60:
                risk_penalty += 15 * weight
            elif top10_pct >= 40:
                risk_penalty += 5 * weight

        # ── Social sentiment ──
        if r.get("is_suspicious"):
            suspicion_count = len(r.get("suspicion_reasons", []))
            risk_penalty += 12 * suspicion_count * weight

        # ── Token age ──
        age_risk_level = r.get("risk_level", "")
        if age_risk_level == "critical":
            risk_penalty += 20 * weight
            severe_risk_count += 1
        elif age_risk_level == "high":
            risk_penalty += 12 * weight
        elif age_risk_level == "medium":
            risk_penalty += 5 * weight
        elif age_risk_level == "low":
            clean_bonus += 5 * weight

        # ── Volume/liquidity ratio (wash trading) ──
        if r.get("is_wash_trading_suspicious"):
            risk_penalty += 20 * weight
            severe_risk_count += 1
            risk_categories.add("wash_trading")
        vol_risk_level = r.get("risk_level", "")
        if vol_risk_level == "critical":
            risk_penalty += 25 * weight
        elif vol_risk_level == "high":
            risk_penalty += 15 * weight

    # ── Inter-module correlation bonus/penalty ──
    # Multiple risk categories = compounding danger
    if len(risk_categories) >= 4:
        risk_penalty *= 1.3  # 30% penalty boost for multi-category risk
    elif len(risk_categories) >= 3:
        risk_penalty *= 1.15  # 15% boost for 3+ categories

    # Compounding severe risks (each additional severe risk adds multiplicative penalty)
    if severe_risk_count >= 3:
        risk_penalty *= 1.4
    elif severe_risk_count >= 2:
        risk_penalty *= 1.2

    # "Too good to be true" detection — suspicious perfection
    if clean_bonus >= 20 and risk_penalty == 0 and modules_with_data < 4:
        # Token looks perfect but we have little data — reduce score
        score = min(score, 55)

    # Apply modifiers
    score = score + clean_bonus - risk_penalty

    # Confidence boost for comprehensive clean scans
    if modules_with_data >= 8 and risk_penalty == 0:
        score = min(95, score + 10)
    elif modules_with_data >= 5 and risk_penalty == 0:
        score = min(90, score + 5)

    # Clamp to 0-100
    return max(0, min(100, int(score)))
