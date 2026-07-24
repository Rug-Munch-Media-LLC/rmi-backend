"""Scanner service — main scan_token orchestrator."""

from __future__ import annotations

import asyncio
from typing import Any

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
from app.domains.scanner.service.scoring import _calculate_safety_score, _ingest_scan_to_rag
from app.domains.scanner.service.stubs import (
    _check_arkham,
    _check_blowfish,
    _check_dune,
    _check_nansen,
)

logger = get_logger(__name__)

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
    ELITE tier:   Tier 1+2 + deep deployer, price consensus, RAG, nansen,
                  blowfish, dune, arkham
    """
    if tiers is None:
        tiers = ["free"]

    result = ScanResult(token_address=token_address, chain=chain)

    last_error: str | None = None

    # Run modules in parallel
    async def run_module(name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal last_error
        try:
            r = await func(*args, **kwargs)
            result.modules_run.append({"module": name, "status": "success", "result": r})
            return r
        except (ValueError, TypeError, KeyError, OSError) as e :
            logger.debug("run_module_error", exc_info=True)
            logger.warning("module_failed", module=name, error=str(e))
            last_error = str(e)
            result.modules_run.append({"module": name, "status": "error", "error": str(e)})
            return None

    tasks = []

    # ── FREE tier (Tier 1) — core checks (no API keys needed) ──
    if "free" in tiers or "pro" in tiers or "elite" in tiers:
        tasks.append(run_module("market_data", fetch_market_data, token_address, chain))
        tasks.append(run_module("honeypot", _check_honeypot_is, token_address, chain))
        tasks.append(run_module("goplus", _check_goplus_security, token_address, chain))
        tasks.append(run_module("copycat", _check_copycat, token_address, ""))
        tasks.append(run_module("spam", _check_spam, token_address, chain))
        tasks.append(run_module("blockscout", _check_blockscout, token_address, chain))
        tasks.append(run_module("defiscanner", _check_defi_scanner, token_address, chain))
        # New FREE checks
        tasks.append(run_module("token_age", _check_token_age, token_address, chain))
        tasks.append(
            run_module(
                "volume_liquidity_ratio", _check_volume_liquidity_ratio, token_address, chain
            )
        )

    # ── PRO tier (Tier 2) — advanced checks ──
    if "pro" in tiers or "elite" in tiers:
        tasks.append(run_module("deployer", _get_deployer_info, token_address, chain))
        tasks.append(run_module("holders", _get_holder_data, token_address, chain))
        tasks.append(run_module("verification", _check_contract_verification, token_address, chain))
        tasks.append(run_module("liquidity", _check_liquidity_lock_multi, token_address, chain))
        tasks.append(run_module("bundle", _check_bundle, token_address, chain))
        # New PRO checks
        tasks.append(run_module("lp_concentration", _check_lp_concentration, token_address, chain))
        tasks.append(run_module("ownership", _check_ownership_renounced, token_address, chain))
        tasks.append(run_module("social_sentiment", _check_social_sentiment, token_address, chain))

    # ── ELITE tier (Tier 3) — deep analysis ──
    if "elite" in tiers:
        tasks.append(run_module("price", _get_price_consensus, token_address, chain))
        tasks.append(run_module("rag", _rag_scam_check, token_address, chain))
        tasks.append(run_module("nansen", _check_nansen, token_address, chain))
        tasks.append(run_module("blowfish", _check_blowfish, token_address, chain))
        tasks.append(run_module("dune", _check_dune, token_address, chain))
        tasks.append(run_module("arkham", _check_arkham, token_address, chain))

    # ── Run all tasks ──
    await asyncio.gather(*tasks)

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
        if (
            isinstance(risk_score, (int, float))
            and risk_score >= 70
            and module_name in ("goplus", "honeypot")
        ):
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

    # ── Calculate confidence based on how many modules returned data ──
    successful = sum(1 for m in result.modules_run if m.get("status") == "success")
    total = len(result.modules_run) if result.modules_run else 1
    base_confidence = min(100, int((successful / total) * 100))
    # Add bonus for modules with real data (not pending)
    pending_count = sum(
        1
        for m in result.modules_run
        if isinstance(m.get("result"), dict) and m["result"].get("result") == "pending"
    )
    real_count = successful - pending_count
    result.confidence = min(100, base_confidence + real_count * 5)

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
            result.safety_score = int(
                result.safety_score * (1 - fusion_weight) + fused_safety * fusion_weight
            )
            # Also apply high-severity findings as risk flags
            for finding in fused.get("risk_findings", []):
                if finding.get("severity") in ("CRITICAL", "HIGH"):
                    flag_text = finding.get("finding", "")[:60]
                    if flag_text and flag_text not in result.risk_flags:
                        result.risk_flags.append(
                            f"fusion:{finding.get('source', 'unknown')}:{flag_text}"
                        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("data_fusion_skipped", error=str(e)[:100])
        result.fusion_brief = {"summary": "Fusion engine unavailable", "risk_score": None}

    # Ingest to RAG
    await _ingest_scan_to_rag(result)

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
        scorching_find = any(
            h.final_score > 0.7 for h in rag_ctx.similar_scams
        )
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

