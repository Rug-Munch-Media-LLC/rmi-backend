"""
RugCharts Intel — Instant Token Report.
One-call comprehensive intel on any token.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.core.logging import get_logger

from .cache import ARKHAM_KEY, CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")


async def instant_token_report(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Generate a complete intelligence report for any token in a single call.
    Combines: security scan, holder health, liquidity risk, rug pattern matching,
    developer reputation, entity intel, and whale activity.
    This is the ONE endpoint that powers the RugCharts token page.
    """
    if not address:
        return None

    cache_key = f"token_report:{chain}:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    report = {
        "token": address,
        "chain": chain,
        "generated_at": datetime.now(UTC).isoformat(),
        "sections": {},
        "overall_risk": {"score": 0, "level": "UNKNOWN", "color": "#888888"},
        "quick_verdict": "",
    }

    api_key = kw.get("api_key", "") or HELIUS_KEY
    risk_scores = []

    try:
        # Section 1: Security Scan
        try:
            from app.databus.token_security import run_full_scan

            security = await run_full_scan(address, chain, api_key=api_key)
            if security:
                report["sections"]["security"] = {
                    "score": security.get("security_score", 50),
                    "band": security.get("risk_band", "UNKNOWN"),
                    "checks_run": security.get("total_checks_run", 0),
                    "highlights": _extract_security_highlights(security),
                }
                risk_scores.append(security.get("security_score", 50))
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            report["sections"]["security"] = {"error": str(e)[:100]}

        # Section 2: Holder Health
        try:
            from .holders import holder_health_score

            holder_data = await holder_health_score(address, chain, api_key=api_key)
            if holder_data:
                gini = holder_data.get("gini_coefficient", 0)
                gini_risk = (gini / 0.9) * 100 if gini > 0 else 0
                report["sections"]["holders"] = {
                    "total": holder_data.get("total_holders", 0),
                    "gini": gini,
                    "top10_pct": holder_data.get("top10_concentration", 0),
                    "top1_pct": holder_data.get("top1_dominance", 0),
                    "whales": holder_data.get("whale_count", 0),
                    "decentralization": holder_data.get("decentralization_score", 0),
                    "risk": holder_data.get("risk_level", "UNKNOWN"),
                }
                risk_scores.append(gini_risk)
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            report["sections"]["holders"] = {"error": str(e)[:100]}

        # Section 3: Liquidity Risk
        try:
            from .liquidity import liquidity_risk_monitor

            liq_data = await liquidity_risk_monitor(address, chain, api_key=api_key)
            if liq_data:
                report["sections"]["liquidity"] = {
                    "concentration_risk": liq_data.get("lp_concentration_risk", "unknown"),
                    "risk_score": liq_data.get("risk_score", 50),
                    "risk_level": liq_data.get("risk_level", "UNKNOWN"),
                    "lp_holders": len(liq_data.get("lp_holders", [])),
                }
                risk_scores.append(liq_data.get("risk_score", 50))
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            report["sections"]["liquidity"] = {"error": str(e)[:100]}

        # Section 4: Rug Pattern Match
        try:
            from .rug_patterns import rug_pattern_matcher

            rug_data = await rug_pattern_matcher(address, chain, api_key=api_key)
            if rug_data:
                top_match = rug_data.get("pattern_matches", [{}])[0] if rug_data.get("pattern_matches") else {}
                report["sections"]["rug_patterns"] = {
                    "total_matches": rug_data.get("total_matches", 0),
                    "overall_risk": rug_data.get("overall_rug_risk", "LOW"),
                    "top_match": top_match.get("pattern_name", "None"),
                    "top_score": top_match.get("match_score", 0),
                }
                if rug_data.get("overall_rug_risk") == "CRITICAL":
                    risk_scores.append(95)
                elif rug_data.get("overall_rug_risk") == "HIGH":
                    risk_scores.append(75)
                elif rug_data.get("overall_rug_risk") == "MEDIUM":
                    risk_scores.append(45)
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            report["sections"]["rug_patterns"] = {"error": str(e)[:100]}

        # Section 5: Developer Reputation
        try:
            from .developer import developer_reputation

            dev_data = await developer_reputation(address, chain, api_key=api_key)
            if dev_data:
                report["sections"]["developer"] = {
                    "tokens_deployed": dev_data.get("total_tokens_deployed", "unknown"),
                    "activity_span_days": dev_data.get("activity_span_days", 0),
                    "risk_level": dev_data.get("risk_level", "UNKNOWN"),
                    "known_entity": dev_data.get("known_entity", ""),
                }
                dev_risk = {"HIGH": 80, "MEDIUM": 50, "LOW": 20, "UNKNOWN": 40}.get(
                    dev_data.get("risk_level", "UNKNOWN"), 40
                )
                risk_scores.append(dev_risk)
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            report["sections"]["developer"] = {"error": str(e)[:100]}

        # Section 6: Volume Authenticity
        try:
            vol_24h = float(kw.get("volume_24h", 0))
            liq = float(kw.get("liquidity_usd", 0))
            uw = int(kw.get("unique_wallets", 0))
            if vol_24h > 0 or liq > 0:
                from app.databus.volume_authenticity import quick_authenticity_score

                auth = quick_authenticity_score(vol_24h, liq, uw, 0)
                report["sections"]["volume"] = {
                    "fake_volume_pct": auth.get("fake_volume_pct", 0),
                    "authentic_score": auth.get("authentic_score", 100),
                    "risk_level": auth.get("risk_level", "UNKNOWN"),
                }
                risk_scores.append(auth.get("fake_volume_pct", 0))
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            report["sections"]["volume"] = {"error": str(e)[:100]}

        # Section 7: Entity Intel (Arkham)
        if ARKHAM_KEY:
            try:
                from .cross_chain import cross_chain_entity

                entity = await cross_chain_entity(address)
                if entity and entity.get("entity"):
                    report["sections"]["entity"] = {
                        "name": entity["entity"].get("entity_name", ""),
                        "type": entity["entity"].get("entity_type", ""),
                        "label": entity["entity"].get("label", ""),
                        "primary_chain": entity["entity"].get("primary_chain", ""),
                    }
                    if entity["entity"].get("entity_name"):
                        risk_scores.append(5)
            except (ValueError, ConnectionError, TimeoutError, RuntimeError):
                logger.warning("error")

        if risk_scores:
            avg_risk = sum(risk_scores) / len(risk_scores)
            report["overall_risk"]["score"] = round(avg_risk, 1)
            if avg_risk >= 80:
                report["overall_risk"]["level"] = "CRITICAL"
                report["overall_risk"]["color"] = "#FF0044"
            elif avg_risk >= 60:
                report["overall_risk"]["level"] = "HIGH"
                report["overall_risk"]["color"] = "#FF8800"
            elif avg_risk >= 40:
                report["overall_risk"]["level"] = "MEDIUM"
                report["overall_risk"]["color"] = "#FFD700"
            elif avg_risk >= 20:
                report["overall_risk"]["level"] = "LOW"
                report["overall_risk"]["color"] = "#88FF00"
            else:
                report["overall_risk"]["level"] = "SAFE"
                report["overall_risk"]["color"] = "#00FF88"

        sections = report["sections"]
        if sections.get("security", {}).get("band") == "DANGER":
            report["quick_verdict"] = "EXTREME RISK - Multiple critical security failures detected"
        elif report["overall_risk"]["level"] == "CRITICAL":
            report["quick_verdict"] = "HIGH RISK - Multiple red flags. Not recommended."
        elif report["overall_risk"]["level"] == "HIGH":
            report["quick_verdict"] = "ELEVATED RISK - Proceed with caution. Review details."
        elif report["overall_risk"]["level"] == "MEDIUM":
            report["quick_verdict"] = "MODERATE RISK - Standard for new tokens. Monitor closely."
        elif sections.get("entity", {}).get("name"):
            report["quick_verdict"] = f"KNOWN ENTITY - {sections['entity']['name']}. Lower risk."
        else:
            report["quick_verdict"] = "LOW RISK - No significant concerns detected."

        report["sections_count"] = len(report["sections"])
        report["source"] = "instant_token_report"
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        report["error"] = str(e)[:200]
        logger.error(f"Token report failed: {e}")

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["token_report"], json.dumps(report, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return report


def _extract_security_highlights(security: dict) -> list[str]:
    """Extract key security findings for the quick overview."""
    highlights = []
    checks = security.get("checks", {})
    for cid, cr in checks.items():
        if cr.get("status") == "fail":
            highlights.append(f"FAIL: {cr.get('details', cid)}")
        elif cr.get("status") == "warning":
            highlights.append(f"WARN: {cr.get('details', cid)}")
    return highlights[:5]
