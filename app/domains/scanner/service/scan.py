"""Scanner service — main scan_token orchestrator."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.data_fusion import fusion
from app.core.logging import get_logger
from app.domains.scanner.market_data import fetch_market_data
from app.domains.scanner.models import ScanResult
from app.domains.scanner.modules import (
    _check_blockscout,
    _check_bundle,
    _check_contract_verification,
    _check_copycat,
    _check_defi_scanner,
    _check_goplus_security,
    _check_honeypot_is,
    _check_liquidity_lock_multi,
    _check_lp_concentration,
    _check_ownership_renounced,
    _check_social_sentiment,
    _check_spam,
    _check_token_age,
    _check_volume_liquidity_ratio,
    _get_deployer_info,
    _get_holder_data,
    _get_price_consensus,
)
from app.domains.scanner.service.checks import _rag_scam_check
from app.domains.scanner.service.scoring import (
    _calculate_safety_score,
    _ingest_scan_to_rag,
    _risk_level_from_safety,
)

logger = get_logger(__name__)


def _build_module_tasks(
    token_address: str, chain: str, tiers: list[str]
) -> list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]]:
    """Build the list of module scan tasks for the requested tiers.

    FREE tier:    SENTINEL Tier 1  (market_data, honeypot, goplus, copycat, spam,
                                    blockscout, defiscanner, token_age, vol/liq ratio)
    PRO tier:     Tier 1 + deployer, holders, verification, liquidity, bundle,
                  lp_concentration, ownership_renounced, social_sentiment
    ELITE tier:   Tier 1+2 + price consensus, RAG

    Args:
        token_address: token to scan.
        chain: chain name.
        tiers: requested tiers (free/pro/elite).

    Returns:
        list of (module_name, func, args, kwargs) tuples.
    """
    tasks: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]] = []

    # ── FREE tier (Tier 1) — core checks (no API keys needed) ──
    if "free" in tiers or "pro" in tiers or "elite" in tiers:
        tasks.append(("market_data", fetch_market_data, (token_address, chain), {}))
        tasks.append(("honeypot", _check_honeypot_is, (token_address, chain), {}))
        tasks.append(("goplus", _check_goplus_security, (token_address, chain), {}))
        tasks.append(("copycat", _check_copycat, ("", "", token_address, chain), {}))
        tasks.append(("spam", _check_spam, (token_address, chain), {}))
        tasks.append(("blockscout", _check_blockscout, (token_address, chain), {}))
        tasks.append(("defiscanner", _check_defi_scanner, (token_address, chain), {}))
        # New FREE checks
        tasks.append(("token_age", _check_token_age, (token_address, chain), {}))
        tasks.append(("volume_liquidity_ratio", _check_volume_liquidity_ratio, (token_address, chain), {}))

    # ── PRO tier (Tier 2) — advanced checks ──
    if "pro" in tiers or "elite" in tiers:
        tasks.append(("deployer", _get_deployer_info, (token_address, chain), {}))
        tasks.append(("holders", _get_holder_data, (token_address, chain), {}))
        tasks.append(("verification", _check_contract_verification, (token_address, chain), {}))
        tasks.append(("liquidity", _check_liquidity_lock_multi, (token_address, chain), {}))
        tasks.append(("bundle", _check_bundle, (token_address, chain), {}))
        # New PRO checks
        tasks.append(("lp_concentration", _check_lp_concentration, (token_address, chain), {}))
        tasks.append(("ownership", _check_ownership_renounced, (token_address, chain), {}))
        tasks.append(("social_sentiment", _check_social_sentiment, (token_address, chain), {}))

    # ── ELITE tier (Tier 3) — deep analysis ──
    if "elite" in tiers:
        tasks.append(("price", _get_price_consensus, (token_address, chain), {}))
        tasks.append(("rag", _rag_scam_check, (token_address, chain), {}))

    return tasks


async def _execute_module_tasks(
    tasks: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Run module tasks in parallel, collecting per-module results.

    Args:
        tasks: task list from _build_module_tasks.

    Returns:
        list of {"module": name, "status": "success"|"error", ...} entries.
    """
    modules_run: list[dict[str, Any]] = []
    last_error: str | None = None

    async def run_module(name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal last_error
        try:
            r = await func(*args, **kwargs)
            modules_run.append({"module": name, "status": "success", "result": r})
            return r
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
            logger.debug("run_module_error", exc_info=True)
            logger.warning("module_failed", module=name, error=str(e))
            last_error = str(e)
            modules_run.append({"module": name, "status": "error", "error": str(e)})
            return None

    await asyncio.gather(*(run_module(name, func, *args, **kwargs) for name, func, args, kwargs in tasks))
    return modules_run


def _compute_scan_confidence(results: list[dict[str, Any]], tier: str) -> int:
    """Calculate scan confidence based on modules that actually returned real data.

    Modules returning "pending"/"unavailable" or that errored contribute nothing,
    so confidence reflects only modules with actionable results.

    Args:
        results: modules_run list from the scan.
        tier: requested tier ("free", "pro", "elite") — reserved for future tier weighting.

    Returns:
        confidence percentage 0-100.
    """
    total = len(results) if results else 1
    real_count = sum(
        1
        for m in results
        if m.get("status") == "success"
        and isinstance(m.get("result"), dict)
        and m["result"].get("result") not in ("pending", "unavailable", "no_data")
    )
    base_confidence = min(100, int((real_count / total) * 100))
    # Small bonus per module that returned real data
    return min(100, base_confidence + real_count * 5)


async def scan_token(
    token_address: str,
    chain: str = "ethereum",
    tiers: list[str] | None = None,
    include_market_data: bool = True,
    include_social: bool = True,
) -> ScanResult:
    """Scan a token for security issues across ALL SENTINEL modules.

    FREE tier:    SENTINEL Tier 1  (market_data, honeypot, goplus, copycat, spam,
                                    blockscout, defiscanner, token_age, vol/liq ratio)
    PRO tier:     Tier 1 + deployer, holders, verification, liquidity, bundle,
                  lp_concentration, ownership_renounced, social_sentiment
    ELITE tier:   Tier 1+2 + deep deployer, price consensus, RAG
    """
    if tiers is None:
        tiers = ["free"]

    result = ScanResult(token_address=token_address, chain=chain)

    # Build and run the module task list for the requested tiers
    tasks = _build_module_tasks(token_address, chain, tiers)
    result.modules_run = await _execute_module_tasks(tasks)

    # ── Calculate real safety score from module results ──
    result.safety_score = _calculate_safety_score(result.modules_run)

    # ── Generate risk flags ──
    risk_flags: list[str] = []
    for mod_entry in result.modules_run:
        r = mod_entry.get("result")
        if not isinstance(r, dict):
            continue

        module_name = mod_entry.get("module", "")

        if r.get("is_honeypot"):
            risk_flags.append("honeypot")
        if r.get("is_spam"):
            risk_flags.append("flagged_spam")
        if r.get("is_sanctioned"):
            risk_flags.append("sanctioned_address")
        if r.get("is_bundled"):
            risk_flags.append("bundled_launch")
        if r.get("result") == "high_risk" and module_name == "copycat":
            risk_flags.append("copycat_token")
        if r.get("reputation_label") == "CRITICAL":
            risk_flags.append("malicious_deployer")
        if r.get("reputation_label") == "HIGH":
            risk_flags.append("high_risk_deployer")

        risk_score = r.get("risk_score", 0)
        if isinstance(risk_score, (int, float)) and risk_score >= 70 and module_name in ("goplus", "honeypot"):
            risk_flags.append("high_risk_score")

        # Concentration (existing)
        if isinstance(r.get("concentration_pct"), (int, float)) and r["concentration_pct"] > 80:
            risk_flags.append("concentrated_holders")

        # ── New check risk flags ──
        # Token age
        if r.get("risk_level") == "critical" and module_name == "token_age":
            risk_flags.append("new_token_under_1h")
        elif r.get("is_new_token") and module_name == "token_age":
            risk_flags.append("token_under_24h")

        # LP concentration
        if module_name == "lp_concentration":
            if r.get("risk_level") == "critical":
                risk_flags.append("extreme_lp_concentration")
            elif r.get("is_concentrated"):
                risk_flags.append("concentrated_lp")

        # Ownership
        if module_name == "ownership":
            if r.get("risk_level") == "active_proxy_owner":
                risk_flags.append("active_proxy_owner")
            elif r.get("risk_level") == "active_owner":
                risk_flags.append("active_owner")

        # Social sentiment
        if r.get("is_suspicious") and module_name == "social_sentiment":
            risk_flags.append("suspicious_social_sentiment")

        # Volume/liquidity ratio
        if r.get("is_wash_trading_suspicious") and module_name == "volume_liquidity_ratio":
            risk_flags.append("wash_trading_suspected")
        if module_name == "volume_liquidity_ratio" and r.get("risk_level") == "critical":
            risk_flags.append("extreme_volume_liquidity_ratio")

    result.risk_flags = risk_flags

    # ── Push Telegram alert for high/critical risk (or severe red flags) ──
    try:
        from app.domains.telegram.alert_hooks import send_scanner_alert, should_send_scanner_alert

        scan_risk_level = _risk_level_from_safety(float(result.safety_score)) or ""
        if should_send_scanner_alert(scan_risk_level, list(risk_flags)):
            await send_scanner_alert(
                token_address=token_address,
                chain=chain,
                risk_score=100 - float(result.safety_score),
                threats=list(risk_flags),
                token_name=getattr(result, "name", "") or "Unknown",
                token_symbol=getattr(result, "symbol", "") or "???",
            )
    except Exception as exc:
        logger.debug("scanner_alert_failed", error=str(exc)[:100])

    # ── Calculate confidence based on modules that actually ran and returned real data ──
    result.confidence = _compute_scan_confidence(result.modules_run, tiers[0] if tiers else "free")

    # ── Record own-data coverage metrics ──
    try:
        from app.domains.scanner.metrics import record_token_scan_coverage

        data_sources = [
            m.get("result", {}).get("source", "") if isinstance(m.get("result"), dict) else ""
            for m in result.modules_run
        ]
        record_token_scan_coverage(data_sources, result.modules_run)
    except Exception as exc:
        logger.debug("coverage_metrics_failed", error=str(exc)[:120])

    # ── Fuse token risk via DataFusion engine ──
    # Extract module results for the fusion engine to cross-reference
    goplus_result: dict[str, Any] | None = None
    dexscreener_result: dict[str, Any] | None = None
    honeypot_result: dict[str, Any] | None = None
    sentinel_result: dict[str, Any] | None = None

    for mod_entry in result.modules_run:
        r = mod_entry.get("result")
        if not isinstance(r, dict):
            continue
        module_name = mod_entry.get("module", "")
        if module_name == "goplus" and r.get("result") not in ("error", "unavailable"):
            goplus_result = r
        elif module_name == "defiscanner" and r.get("result") not in ("error", "unavailable"):
            dexscreener_result = r
        elif module_name == "honeypot" and r.get("result") not in ("error", "unavailable"):
            honeypot_result = r
        elif module_name == "market_data":
            sentinel_result = r

    # Run fusion — uses weighted voting, outlier removal, freshness decay
    try:
        fused = await fusion.fuse_token_risk(
            goplus_result=goplus_result,
            dexscreener_result=dexscreener_result,
            honeypot_result=honeypot_result,
            sentinel_result=sentinel_result,
        )
        result.fusion_brief = fusion.generate_intelligence_brief(fused)
        # If fusion confidence is high, adjust safety_score to match
        if fused.get("confidence", 0) > 0.5 and fused.get("fused_value") is not None:
            fused_risk = fused["fused_value"]
            # Convert risk score (0-100, higher=riskier) to safety score (0-100, higher=safer)
            fused_safety = max(0, min(100, 100 - fused_risk))
            # Blend: 70% original scoring, 30% fusion (increase fusion weight with more sources)
            fusion_weight = min(0.5, 0.2 + len(fused.get("sources_used", [])) * 0.05)
            result.safety_score = int(result.safety_score * (1 - fusion_weight) + fused_safety * fusion_weight)
            # Also apply high-severity findings as risk flags
            for finding in fused.get("risk_findings", []):
                if finding.get("severity") in ("CRITICAL", "HIGH"):
                    flag_text = finding.get("finding", "")[:60]
                    if flag_text and flag_text not in result.risk_flags:
                        result.risk_flags.append(f"fusion:{finding.get('source', 'unknown')}:{flag_text}")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("data_fusion_skipped", error=str(e)[:100])
        result.fusion_brief = {"summary": "Fusion engine unavailable", "risk_score": None}

    # Ingest to RAG
    await _ingest_scan_to_rag(result)

    # ── Auto-label high-risk deployer wallets ──
    if "malicious_deployer" in risk_flags or "high_risk_deployer" in risk_flags:
        deployer_addr: str | None = None
        deployer_risk: float = 0.0
        deployer_label: str = "UNKNOWN"
        for mod_entry in result.modules_run:
            r = mod_entry.get("result")
            if not isinstance(r, dict):
                continue
            if mod_entry.get("module") == "deployer":
                deployer_addr = r.get("deployer_address", "")
                deployer_risk = float(r.get("reputation_score", 50) or 50)
                deployer_label = str(r.get("reputation_label", "UNKNOWN"))

        if deployer_addr and deployer_addr != "unknown":
            from app.domains.scanner.shared.deployer_labeler import auto_label_deployer

            await auto_label_deployer(
                deployer_address=deployer_addr,
                chain=chain,
                risk_score=deployer_risk,
                risk_label=deployer_label,
                token_address=token_address,
                token_name=getattr(result, "name", ""),
                token_symbol=getattr(result, "symbol", ""),
                safety_score=result.safety_score,
                risk_flags=risk_flags,
                source="scanner_token_scan",
            )

    # ── Enrich with RAG intelligence ──
    try:
        from app.rag.intelligence import RAGIntelligenceEnricher

        enricher = RAGIntelligenceEnricher()
        rag_ctx = await enricher.enrich_scan_result(result)
        result.rag_context = {
            "similar_scams": [
                {
                    "content": h.content[:200],
                    "score": round(h.final_score or h.score, 4),
                    "collection": h.collection,
                    "doc_id": h.doc_id,
                }
                for h in rag_ctx.similar_scams[:5]
            ],
            "wallet_context": [
                {
                    "content": h.content[:200],
                    "score": round(h.final_score or h.score, 4),
                    "collection": h.collection,
                }
                for h in rag_ctx.wallet_context[:3]
            ],
            "market_signals": len(rag_ctx.market_context),
            "fusion_summary": rag_ctx.fusion_summary,
            "collections_searched": rag_ctx.collections_searched,
            "took_ms": rag_ctx.took_ms,
            "query_id": rag_ctx.query_id,
        }
        # Log what RAG contributed
        scam_count = len(rag_ctx.similar_scams)
        scorching_find = any(h.final_score > 0.7 for h in rag_ctx.similar_scams)
        logger.info(
            "rag_enrichment_done",
            token=token_address[:16],
            similar_scams=scam_count,
            scorching_find=scorching_find,
            took_ms=rag_ctx.took_ms,
        )
        # If RAG found a highly similar scam, add it as a risk flag
        if scorching_find:
            top_match = rag_ctx.similar_scams[0]
            label = top_match.metadata.get("pattern_name", "") or "rag_matched_scam"
            flag = f"rag:{label}"
            if flag not in result.risk_flags:
                result.risk_flags.append(flag)
    except ImportError:
        logger.debug("rag_enrichment_skipped (RAGIntelligenceEnricher not available)")
    except Exception as exc:
        logger.warning("rag_enrichment_failed", token=token_address[:16], error=str(exc)[:100])

    return result


async def quick_scan_text(token_address: str, chain: str = "solana") -> str:
    """Quick text scan for token."""
    result = await scan_token(token_address, chain, tiers=["free", "pro"])
    return (
        f"Token {token_address} scanned on {chain}. "
        f"Safety score: {result.safety_score}/100. "
        f"Confidence: {result.confidence}%. "
        f"Flags: {', '.join(result.risk_flags) if result.risk_flags else 'None'}"
    )
