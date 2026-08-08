"""

Intelligence Panel Router
=========================
Endpoints for the intelligence feed panel on rugmunch.io/intelligence

Data sources:
  - News service (200+ crypto news sources, 15 tiers)
  - Security alerts (rug detection, whale tracking)
  - Prediction market signals (Polymarket, Kalshi, Limitless, Manifold)
  - Crypto Fear Index (aggregated risk from prediction markets)
  - Bulletin posts (community intelligence)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence-panel"])

# Import prediction market intelligence (LAZY - deferred to first request)
pm_intel = None
_pm_intel_loaded = False


def _get_pm_intel() -> Any:
    global pm_intel, _pm_intel_loaded
    if _pm_intel_loaded:
        return pm_intel
    _pm_intel_loaded = True
    try:
        from app.services.prediction_market_intel import (
            get_prediction_market_intel,
        )

        pm_intel = get_prediction_market_intel()
    except ImportError:
        pm_intel = None
    return pm_intel


class IntelSignalResponse(BaseModel):
    signal_type: str
    severity: str
    headline: str
    insight: str
    action: str
    confidence: float
    generated_at: str


class FearIndexResponse(BaseModel):
    overall_risk: float
    exploit_risk: float
    regulatory_risk: float
    stablecoin_risk: float
    exchange_risk: float
    generated_at: str


class IntelligenceItem(BaseModel):
    id: str
    title: str
    content: str | None = None
    url: str | None = None
    source: str
    published_at: str
    category: str
    kind: str
    highlight: bool | None = False
    severity: str | None = None


class IntelligenceFeedResponse(BaseModel):
    items: list[IntelligenceItem]
    count: int
    timestamp: str


# ── Prediction Market Intelligence ─────────────────────────


@router.get("/prediction-signals", response_model=list[IntelSignalResponse])
async def get_prediction_signals(limit: int = Query(10, ge=1, le=20)) -> list[Any]:
    """Get actionable intelligence signals from prediction markets.

    Signal types:
      - HIGH_CONSENSUS_THREAT: Markets strongly expect an adverse event
      - LOW_CONSENSUS_RISK: Markets dismiss a feared risk
      - TOKEN_RISK_SIGNAL: Specific token flagged by prediction markets
      - ECOSYSTEM_RISK: Broad ecosystem-level threat indicator

    Each signal includes: severity, plain-English insight, recommended action.
    """
    _get_pm_intel()
    if not pm_intel:
        return []

    signals = await pm_intel.generate_signals(max_signals=limit)
    return [
        IntelSignalResponse(
            signal_type=s.signal_type,
            severity=s.severity,
            headline=s.headline,
            insight=s.insight,
            action=s.action,
            confidence=round(s.confidence, 2),
            generated_at=s.generated_at,
        )
        for s in signals
    ]


@router.get("/crypto-fear-index", response_model=FearIndexResponse)
async def get_crypto_fear_index() -> Any:
    """Get the Crypto Fear Index - risk aggregated from prediction markets.

    Components:
      - overall_risk: Weighted aggregate of all categories
      - exploit_risk: DeFi hack/exploit/vulnerability probability
      - regulatory_risk: SEC/CFTC/enforcement action probability
      - stablecoin_risk: Depeg/collapse probability
      - exchange_risk: Insolvency/breach probability

    Derived from live prediction market data across Polymarket, Kalshi, Limitless, Manifold.
    Higher = more risk priced in by traders with real money at stake.
    """
    _get_pm_intel()
    if not pm_intel:
        return FearIndexResponse(
            overall_risk=0,
            exploit_risk=0,
            regulatory_risk=0,
            stablecoin_risk=0,
            exchange_risk=0,
            generated_at=datetime.now(UTC).isoformat(),
        )

    fear = await pm_intel.generate_crypto_fear_index()
    return FearIndexResponse(
        overall_risk=fear.overall_risk,
        exploit_risk=fear.exploit_risk,
        regulatory_risk=fear.regulatory_risk,
        stablecoin_risk=fear.stablecoin_risk,
        exchange_risk=fear.exchange_risk,
        generated_at=fear.generated_at,
    )


# ── Aggregated Feed ────────────────────────────────────────


@router.get("/feed", response_model=IntelligenceFeedResponse)
async def get_intelligence_feed(limit: int = Query(20, ge=1, le=50)) -> Any:
    """Get aggregated intelligence feed combining all sources."""
    _get_pm_intel()
    items = []
    now_ts = datetime.now(UTC).isoformat()

    # 1. Prediction market signals (highest priority - forward-looking)
    if pm_intel:
        try:
            signals = await pm_intel.generate_signals(max_signals=5)
            for s in signals:
                severity = s.severity
                items.append(
                    IntelligenceItem(
                        id=f"pm-{s.signal_type}-{now_ts}",
                        title=s.headline,
                        content=s.insight,
                        url="/intelligence",
                        source=f"Prediction Markets ({s.signal_type.replace('_', ' ').title()})",
                        published_at=now_ts,
                        category="prediction-market",
                        kind="signal",
                        highlight=(severity in ("critical", "high")),
                        severity=severity,
                    )
                )
        except Exception as e:
            logger.info(f"Prediction market signals unavailable: {e}")
    # 2. Crypto Fear Index summary
    if pm_intel:
        try:
            fear = await pm_intel.generate_crypto_fear_index()
            risk_label = (
                "EXTREME FEAR"
                if fear.overall_risk >= 75
                else "FEAR"
                if fear.overall_risk >= 50
                else "NEUTRAL"
                if fear.overall_risk >= 25
                else "GREED"
                if fear.overall_risk >= 10
                else "EXTREME GREED"
            )
            items.append(
                IntelligenceItem(
                    id=f"fear-index-{now_ts}",
                    title=f"Crypto Fear Index: {fear.overall_risk:.0f}/100 - {risk_label}",
                    content=(
                        f"Exploit risk: {fear.exploit_risk:.0f}/100 | "
                        f"Regulatory: {fear.regulatory_risk:.0f}/100 | "
                        f"Stablecoin: {fear.stablecoin_risk:.0f}/100 | "
                        f"Exchange: {fear.exchange_risk:.0f}/100. "
                        f"Derived from live Polymarket + Kalshi + Manifold prediction markets."
                    ),
                    url="/intelligence",
                    source="Crypto Fear Index",
                    published_at=now_ts,
                    category="risk-index",
                    kind="index",
                    highlight=(fear.overall_risk >= 50),
                    severity="high" if fear.overall_risk >= 50 else "medium",
                )
            )
        except Exception as e:
            logger.info(f"Crypto Fear Index unavailable: {e}")
    # 3. Prediction market data sources status
    items.append(
        IntelligenceItem(
            id=f"sources-{now_ts}",
            title="Prediction Market Intelligence Active - 4 Sources Live",
            content="RMI now monitors Polymarket, Kalshi, Limitless, and Manifold for crypto security signals. Live risk indicators, token threat detection, and ecosystem monitoring are online.",
            url="/intelligence",
            source="RMI System",
            published_at=now_ts,
            category="system",
            kind="status",
            highlight=False,
        )
    )

    return IntelligenceFeedResponse(
        items=items[:limit],
        count=min(len(items), limit),
        timestamp=now_ts,
    )


@router.get("/latest")
async def get_latest_intelligence() -> Any:
    """Get latest intelligence snapshot for quick updates."""
    _get_pm_intel()
    latest = {
        "timestamp": datetime.now(UTC).isoformat(),
    }

    if pm_intel:
        try:
            signals = await pm_intel.generate_signals(max_signals=3)
            if signals:
                top = signals[0]
                latest["top_signal"] = {
                    "headline": top.headline,
                    "severity": top.severity,
                    "insight": top.insight,
                    "action": top.action,
                }

            fear = await pm_intel.generate_crypto_fear_index()
            latest["fear_index"] = {
                "overall": fear.overall_risk,
                "exploit": fear.exploit_risk,
                "regulatory": fear.regulatory_risk,
                "stablecoin": fear.stablecoin_risk,
                "exchange": fear.exchange_risk,
            }
        except Exception as e:
            latest["error"] = str(e)

    return latest
