"""Payment processing endpoints — human wallet payments and super-tools."""

from __future__ import annotations

import json
import time as _time
from datetime import UTC, datetime
from typing import Any

import aiohttp
import httpx
from fastapi import HTTPException

from app.core.logging import get_logger
from app.domains.billing.x402.shared import (
    HUMAN_PAY_TO,
    HUMAN_PAYMENT_TOKENS,
    AuditRequest,
    MemeVibeRequest,
    SmartMoneyAlphaRequest,
)

from .core import sub_router
from .utils import (
    record_payment,
    run_super_tool_batch,
    verify_onchain_direct,
)

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
# Payment Methods & Human Execution
# ═══════════════════════════════════════════════════════════════


@sub_router.get("/payment-methods")
async def get_payment_methods() -> dict[str, Any]:
    """List all payment methods available for human wallet payments."""
    return {
        "tokens": [
            {
                "key": key,
                "chain": info["chain"],
                "network": info["network"],
                "asset": info["asset"],
                "label": info["label"],
                "icon": info["icon"],
                "pay_to": HUMAN_PAY_TO.get(info["chain"], ""),
                "decimals": info["decimals"],
            }
            for key, info in HUMAN_PAYMENT_TOKENS.items()
        ],
        "pay_to_addresses": {chain: addr for chain, addr in HUMAN_PAY_TO.items() if addr},
        "chain_count": len({i["chain"] for i in HUMAN_PAYMENT_TOKENS.values()}),
        "token_count": len(HUMAN_PAYMENT_TOKENS),
    }


@sub_router.post("/human-execute")
async def human_execute(req: Any) -> dict[str, Any]:
    """Execute a tool after human wallet payment verification.

    Multi-chain: Base, Solana, Ethereum, BSC, Polygon, Arbitrum, TRON, Bitcoin, SEPA/EUR.
    """
    from app.domains.billing.x402.shared import HumanPaymentRequest

    req = HumanPaymentRequest.model_validate(req) if isinstance(req, dict) else req

    tool_name = req.tool
    token_info = HUMAN_PAYMENT_TOKENS.get(req.payment_token)

    if not token_info:
        return {
            "success": False,
            "error": f"Unsupported payment token: {req.payment_token}",
            "supported_tokens": list(HUMAN_PAYMENT_TOKENS.keys()),
        }

    chain = req.chain or token_info["chain"]
    expected_pay_to = HUMAN_PAY_TO.get(chain, "")

    # ── Payment Verification ──
    verified = False
    verification_method = "unknown"
    facilitator_used = None

    try:
        from app.domains.billing.facilitators.router import get_facilitator_router

        router = get_facilitator_router()
        payload = {
            "x402Version": 2,
            "txHash": req.tx_hash,
            "payer": req.wallet,
            "accepted": {
                "network": token_info["network"],
                "asset": token_info["asset"],
                "amount": "0",
                "payTo": expected_pay_to,
            },
        }
        result = await router.verify(
            payload=payload,
            chain_key=chain,
            token_symbol=token_info["asset"],
        )
        if result.verified:
            verified = True
            verification_method = f"facilitator:{result.facilitator}"
            facilitator_used = result.facilitator
    except ImportError:
        pass
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Payment verification via facilitator failed: {e}")

    if not verified:
        try:
            verified = await verify_onchain_direct(req.tx_hash, chain, token_info, expected_pay_to)
            verification_method = "onchain-direct"
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"On-chain direct verification failed: {e}")

    if not verified:
        return {
            "success": False,
            "error": "Payment verification failed. Transaction not confirmed or wrong recipient.",
            "tx_hash": req.tx_hash,
            "chain": chain,
            "expected_pay_to": expected_pay_to[:10] + "...",
        }

    # ── Anti-abuse: check trial limits ──
    from app.routers.x402_enforcement import check_trial

    _can_trial, _remaining = check_trial(tool_name, req.wallet)

    # ── Execute the tool ──
    try:
        if chain in ("base", "ethereum", "bsc", "polygon", "arbitrum") or chain == "tron":
            gw = "https://base.rugmunch.io"
        else:
            gw = "https://sol.rugmunch.io"

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{gw}/tools/{tool_name}",
                json=req.arguments,
                headers={
                    "Content-Type": "application/json",
                    "X-RMI-Human-Payment": "verified",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp,
        ):
            text = await resp.text()
            try:
                result_data = json.loads(text)
            except json.JSONDecodeError:
                result_data = {"raw": text[:500]}

            try:
                from app.routers.x402_enforcement import get_redis

                r = get_redis()
                if r:
                    r.setex(
                        f"x402:spent_tx:{req.tx_hash}",
                        86400,
                        json.dumps(
                            {
                                "chain": chain,
                                "payer": req.wallet,
                                "amount": "0",
                                "tool": tool_name,
                                "timestamp": _time.time(),
                                "method": "human-wallet",
                                "facilitator": facilitator_used,
                            }
                        ),
                    )
            except (ConnectionError, TimeoutError, OSError) as e:
                logger.warning(f"Redis spent_tx record failed: {e}")

            return {
                "success": resp.status < 400,
                "tool": tool_name,
                "payment_token": req.payment_token,
                "chain": chain,
                "tx_hash": req.tx_hash,
                "verified": True,
                "verification": verification_method,
                "facilitator": facilitator_used,
                "pay_to": expected_pay_to,
                "result": result_data,
            }
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {"success": False, "error": f"Tool execution failed: {e!s}"}


# ═══════════════════════════════════════════════════════════════
# TOOL 36: Comprehensive Audit (Super Tool) ($0.15)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/comprehensive_audit")
async def comprehensive_audit(req: AuditRequest) -> Any:
    """One-call deep audit combining rug check, forensics, social, and whale analysis."""
    try:
        BASE = "http://localhost:8000/api/v1/x402-tools"
        results = await run_super_tool_batch(
            {
                "rug": f"{BASE}/rugshield",
                "forensics": f"{BASE}/forensics",
                "social": f"{BASE}/sentiment",
                "whale": f"{BASE}/whale",
            },
            {"address": req.address, "chain": req.chain},
        )

        rug_data = results.get("rug")
        forensics_data = results.get("forensics")
        social_data = results.get("social")
        whale_data = results.get("whale")

        risk_score = 50
        factors: list[str] = []

        if rug_data and rug_data.get("is_honeypot"):
            risk_score += 30
            factors.append("CRITICAL: Potential Honeypot detected")

        if rug_data and not rug_data.get("liquidity_locked"):
            risk_score += 15
            factors.append("WARNING: Liquidity not locked")

        if forensics_data:
            liq_usd = forensics_data.get("liquidity_usd", 0)
            if liq_usd > 0 and liq_usd < 1000:
                risk_score += 20
                factors.append(f"LOW LIQUIDITY: Only ${liq_usd:,.2f} locked")
            vol_24h = forensics_data.get("volume_24h", 0)
            if liq_usd > 0 and vol_24h > liq_usd * 5:
                risk_score += 10
                factors.append("High volume/liquidity ratio (possible wash trading)")

        if social_data:
            sentiment_score = social_data.get("sentiment_score", 0)
            if sentiment_score > 0.7:
                risk_score -= 10
                factors.append("Positive social sentiment")
            elif sentiment_score < 0.3:
                risk_score += 10
                factors.append("Negative social sentiment")

        if whale_data:
            whale_count = whale_data.get("whale_count", 0)
            if whale_count > 0:
                risk_score -= 5
                factors.append(f"{whale_count} whale(s) detected")

        risk_score = max(0, min(100, risk_score))

        if risk_score >= 70:
            recommendation = "HIGH RISK / AVOID"
        elif risk_score >= 50:
            recommendation = "CAUTION / HIGH VOLATILITY"
        else:
            recommendation = "RELATIVELY SAFE / MONITOR"

        result = {
            "tool": "Comprehensive Audit",
            "address": req.address,
            "chain": req.chain,
            "timestamp": datetime.now(UTC).isoformat(),
            "summary": {
                "risk_score": risk_score,
                "recommendation": recommendation,
                "factors": factors,
            },
            "sub_reports": {
                "rug_check": rug_data,
                "forensics": forensics_data,
                "social": social_data,
                "whale_analysis": whale_data,
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        await record_payment("comprehensive_audit", "0.15", req.address)
        return result

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/smart_money_alpha")
async def smart_money_alpha(req: SmartMoneyAlphaRequest) -> Any:
    """One-call smart money signal combining wallet analysis, smart money tracking, and cluster/sybil detection."""
    try:
        BASE = "http://localhost:8000/api/v1/x402-tools"
        results = await run_super_tool_batch(
            {
                "wallet": f"{BASE}/wallet",
                "smartmoney": f"{BASE}/smartmoney",
                "cluster": f"{BASE}/cluster",
                "insider": f"{BASE}/insider",
            },
            {"address": req.wallet, "chain": req.chain},
        )

        wallet_data = results.get("wallet")
        smartmoney_data = results.get("smartmoney")
        cluster_data = results.get("cluster")
        insider_data = results.get("insider")

        alpha_score = 50
        signals: list[str] = []

        if wallet_data:
            tx_count = wallet_data.get("tx_count", 0)
            if tx_count > 1000:
                alpha_score += 10
                signals.append(f"Active wallet ({tx_count}+ transactions)")
            profit_ratio = wallet_data.get("profit_ratio", 0)
            if profit_ratio > 0.5:
                alpha_score += 15
                signals.append(f"Profitable trader ({profit_ratio:.0%} win rate)")

        if smartmoney_data:
            sm_alerts = smartmoney_data.get("smart_money_alerts", [])
            if sm_alerts:
                alpha_score += 10
                signals.append(f"{len(sm_alerts)} active smart money movements")

        if cluster_data:
            cluster_size = cluster_data.get("cluster_size", 0)
            if cluster_size > 5:
                alpha_score -= 10
                signals.append(f"Large cluster ({cluster_size} wallets) - possible sybil")
            if cluster_data.get("is_sybil", False):
                alpha_score -= 20
                signals.append("SYBIL DETECTED - wallet part of coordinated group")

        if insider_data and insider_data.get("insider_detected", False):
            alpha_score += 20
            signals.append("INSIDER ACTIVITY - wallet linked to early token access")

        alpha_score = max(0, min(100, alpha_score))

        if alpha_score >= 75:
            verdict = "STRONG ALPHA - high-value wallet signals"
        elif alpha_score >= 60:
            verdict = "MODERATE ALPHA - watch for follow-up"
        elif alpha_score <= 30:
            verdict = "LOW SIGNAL - avoid or monitor passively"
        else:
            verdict = "NEUTRAL"

        result = {
            "tool": "Smart Money Alpha",
            "wallet": req.wallet,
            "chain": req.chain,
            "timestamp": datetime.now(UTC).isoformat(),
            "summary": {
                "alpha_score": alpha_score,
                "verdict": verdict,
                "signals": signals,
            },
            "sub_reports": {
                "wallet_analysis": wallet_data,
                "smart_money": smartmoney_data,
                "cluster_analysis": cluster_data,
                "insider_detection": insider_data,
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        await record_payment("smart_money_alpha", "0.25", req.wallet)
        return result

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/meme_vibe_score")
async def meme_vibe_score(req: MemeVibeRequest) -> Any:
    """One-call meme token vibe check combining sentiment, social signals, and launch analysis."""
    try:
        BASE = "http://localhost:8000/api/v1/x402-tools"
        results = await run_super_tool_batch(
            {
                "sentiment": f"{BASE}/sentiment",
                "social": f"{BASE}/social_signal",
                "launch": f"{BASE}/launch?address={req.token}&chain={req.chain}",
            },
            {"token": req.token, "chain": req.chain},
        )

        # Re-fetch launch via GET with query params
# httpx available at module level

        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{BASE}/launch", params={"address": req.token, "chain": req.chain})
            launch_data = r.json() if r.status_code == 200 else results.get("launch")

        sentiment_data = results.get("sentiment")
        social_data = results.get("social")

        vibe_score = 50
        vibes: list[str] = []

        if sentiment_data:
            score = sentiment_data.get("sentiment_score", 0)
            if score > 0.7:
                vibe_score += 15
                vibes.append("Strong positive sentiment")
            elif score < 0.3:
                vibe_score -= 15
                vibes.append("Negative sentiment detected")
            trend = sentiment_data.get("sentiment_trend", "")
            if trend:
                vibes.append(f"Sentiment trend: {trend}")

        if social_data:
            engagement = social_data.get("engagement_score", 0)
            bot_ratio = social_data.get("bot_ratio", 0)
            if engagement > 0.7:
                vibe_score += 10
                vibes.append("High social engagement")
            if bot_ratio > 0.5:
                vibe_score -= 20
                vibes.append(f"High bot ratio ({bot_ratio:.0%}) - likely artificial hype")

        if launch_data:
            bonding = launch_data.get("bonding_curve_progress", 0)
            if bonding > 0.8:
                vibe_score += 10
                vibes.append("Bonding curve near completion - strong launch momentum")
            elif bonding < 0.2:
                vibe_score -= 10
                vibes.append("Early bonding curve - high risk")
            if launch_data.get("is_fair_launch", False):
                vibe_score += 5
                vibes.append("Fair launch confirmed")

        vibe_score = max(0, min(100, vibe_score))

        if vibe_score >= 75:
            verdict = "VIBE CHECK PASSED - organic momentum"
        elif vibe_score >= 50:
            verdict = "MIXED VIBES - monitor closely"
        elif vibe_score >= 30:
            verdict = "WEAK VIBES - high risk of dump"
        else:
            verdict = "RUG VIBES - likely artificial/scam"

        result = {
            "tool": "Meme Vibe Score",
            "token": req.token,
            "chain": req.chain,
            "timestamp": datetime.now(UTC).isoformat(),
            "summary": {
                "vibe_score": vibe_score,
                "verdict": verdict,
                "vibes": vibes,
            },
            "sub_reports": {
                "sentiment": sentiment_data,
                "social_signals": social_data,
                "launch_analysis": launch_data,
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        await record_payment("meme_vibe_score", "0.01", req.token)
        return result

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
