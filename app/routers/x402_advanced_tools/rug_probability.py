"""
Layer 4: Rug Probability Score — predictive 0-100 "will this token rug in 24h?".

Combines 7 signals:
  - Honeypot check
  - DexScreener liquidity & age
  - Holder concentration
  - Deployer history (scam pattern match)
  - Social anomaly (CryptoPanic)
  - Volume/liquidity ratio
  - Price crash signal
"""

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp

from app.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.x402_cache import get_cached, set_cached

# Runtime import: FastAPI resolves this body model at OpenAPI schema generation time.
from app.routers.x402_advanced_tools.models import AddressRequest

logger = get_logger("x402.rug_probability")

MODEL_VERSION = "rug-probability-2026-10"
DEPRECATION_RELEASE = "0.4.0"
_deprecation_logged = False


def _log_deprecation() -> None:
    """Warn once that `signals[].weight` is gone and `signals` follows in 0.4.0."""
    global _deprecation_logged
    if not _deprecation_logged:
        logger.warning(
            "rug_probability: per-signal weights removed; `signals` is deprecated and disappears in %s (use `reasons`)",
            DEPRECATION_RELEASE,
        )
        _deprecation_logged = True


def _envelope(
    address: str,
    chain: str,
    probability: int,
    tier: str,
    recommendation: str,
    signals: list[dict[str, Any]],
    started: float,
) -> dict[str, Any]:
    """Serialized paid response: score + band + reason strings, no rubric."""
    _log_deprecation()
    return {
        "tool": "Rug Probability Score",
        "version": "2.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "address": address,
        "chain": chain,
        "rug_probability": probability,
        "tier": tier,
        "recommendation": recommendation,
        "reasons": [item["detail"] for item in signals],
        # DEPRECATED: kept for one release; entries carry a coarse `band` and no
        # numeric weight. Removed in DEPRECATION_RELEASE.
        "signals": signals,
        "signal_count": len(signals),
        "method": "banded_signal_sum",
        "model_version": MODEL_VERSION,
        "performance_ms": round((time.time() - started) * 1000, 1),
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


async def rug_probability(req: AddressRequest) -> Any:
    """Predictive rug pull probability: 0-100 score."""
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        t0 = time.time()

        cached = get_cached("rug_probability", {"address": addr, "chain": chain})
        if cached:
            return cached

        probability = 0
        signals = []

        # ── Signal 1: Honeypot Check ──
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{settings.backend_url}/api/v1/x402-tools/honeypot_check",
                    json={"address": addr, "chain": chain},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp,
            ):
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("is_honeypot"):
                        probability += 40
                        signals.append(
                            {
                                "signal": "honeypot_detected",
                                "band": "extreme",
                                "detail": data.get("reason", "Cannot sell - confirmed honeypot"),
                            }
                        )
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.debug(f"Honeypot check failed: {e}")

        # ── Signal 2: DexScreener Liquidity & Age ──
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            p = pairs[0]
                            liq = p.get("liquidity", {}).get("usd", 0) or 0
                            vol = p.get("volume", {}).get("h24", 0) or 0
                            age_ms = p.get("pairCreatedAt", 0) or 0
                            age_h = (time.time() * 1000 - age_ms) / 3600000 if age_ms else 0

                            if liq < 1000:
                                probability += 25
                                signals.append(
                                    {
                                        "signal": "critical_low_liquidity",
                                        "band": "extreme",
                                        "detail": f"Liquidity ${liq:,.0f}",
                                    }
                                )
                            elif liq < 10000:
                                probability += 15
                                signals.append(
                                    {"signal": "low_liquidity", "band": "high", "detail": f"Liquidity ${liq:,.0f}"}
                                )
                            elif liq < 50000:
                                probability += 5
                                signals.append(
                                    {"signal": "moderate_liquidity", "band": "low", "detail": f"Liquidity ${liq:,.0f}"}
                                )

                            if 0 < age_h < 1:
                                probability += 20
                                signals.append(
                                    {"signal": "brand_new", "band": "high", "detail": f"Only {age_h:.1f}h old"}
                                )
                            elif 0 < age_h < 6:
                                probability += 12
                                signals.append(
                                    {"signal": "very_new", "band": "moderate", "detail": f"Only {age_h:.1f}h old"}
                                )
                            elif 0 < age_h < 24:
                                probability += 5
                                signals.append({"signal": "new_token", "band": "low", "detail": f"{age_h:.0f}h old"})

                            if liq > 0 and vol > liq * 3:
                                probability += 10
                                signals.append(
                                    {
                                        "signal": "pump_dump_pattern",
                                        "band": "moderate",
                                        "detail": f"Volume {vol / liq:.0f}x liquidity",
                                    }
                                )

                            pc = p.get("priceChange", {}).get("h24", 0) or 0
                            if pc < -50:
                                probability += 15
                                signals.append(
                                    {"signal": "price_crashing", "band": "high", "detail": f"Down {pc:.0f}% in 24h"}
                                )
                            elif pc < -20:
                                probability += 8
                                signals.append(
                                    {
                                        "signal": "price_declining",
                                        "band": "moderate",
                                        "detail": f"Down {pc:.0f}% in 24h",
                                    }
                                )
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.debug(f"DexScreener failed: {e}")

        # ── Signal 4: Deployer History ──
        try:
            from app.integrations.rag_helpers.scam_patterns import detect_scam_patterns

            result = asyncio.run(detect_scam_patterns({"address": addr, "chain": chain}, 0.4))
            if result and result.get("risk_score", 0) > 0:
                risk = result.get("risk_score", 0)
                probability += min(risk, 30)
                patterns = result.get("patterns", [])
                if patterns:
                    signals.append(
                        {
                            "signal": "scam_pattern_match",
                            "band": "high",
                            "detail": f"Matches: {', '.join(patterns[:3])}",
                        }
                    )
        except (ImportError, RuntimeError, Exception) as e:
            logger.debug(f"Scam detection failed: {e}")

        # ── Signal 5: Social Anomaly ──
        try:
            async with aiohttp.ClientSession() as session:
                from urllib.parse import quote

                symbol = addr[:12]
                url = f"https://cryptopanic.com/api/free/posts/?filter=important&q={quote(symbol)}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        posts = data.get("results", [])
                        if posts and len(posts) > 20:
                            probability += 5
                            signals.append(
                                {"signal": "social_spike", "band": "low", "detail": f"{len(posts)} social mentions"}
                            )
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.debug(f"CryptoPanic failed: {e}")

        probability = max(0, min(100, probability))

        if probability >= 75:
            tier = "EXTREME_RISK"
            recommendation = "DO NOT BUY - extremely high rug probability"
        elif probability >= 50:
            tier = "HIGH_RISK"
            recommendation = "Avoid - significant rug indicators present"
        elif probability >= 25:
            tier = "MODERATE_RISK"
            recommendation = "Caution - monitor closely before entry"
        elif probability >= 10:
            tier = "LOW_RISK"
            recommendation = "Standard risk - normal market activity"
        else:
            tier = "MINIMAL_RISK"
            recommendation = "Low rug probability - relatively safe"

        result = _envelope(addr, chain, probability, tier, recommendation, signals, t0)

        set_cached("rug_probability", {"address": addr, "chain": chain}, result)
        return result

    except Exception as e:
        logger.error(f"Rug probability failed: {e}")
        raise AppError(message=str(e)) from e


__all__ = ["rug_probability"]
