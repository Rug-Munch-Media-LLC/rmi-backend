"""
from __future__ import annotations

RMI Prediction Market Router
==============================
FastAPI router exposing prediction market data for crypto security intelligence.

Endpoints:
  GET /api/v1/prediction-markets/search          - Search all 4 sources
  GET /api/v1/prediction-markets/trending         - Top markets by volume
  GET /api/v1/prediction-markets/token/{symbol}   - Token-specific markets
  GET /api/v1/prediction-markets/sentinel         - Daily security digest
  GET /api/v1/prediction-markets/detail/{source}/{id}  - Market detail

x402 Tool Registration:
  prediction_market_search   - Search prediction markets (basic tier, $0.01)
  prediction_market_token     - Token-specific odds (standard tier, $0.03)
  prediction_market_sentinel  - Daily threat digest (advanced tier, $0.05)

Caching: Redis-backed, 30s-1hr TTL by endpoint.
Rate limiting: Inherited from app-wide slowapi configuration.
"""

from fastapi import APIRouter, Query

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domains.prediction.prediction_market_service import (
    PredictionDigest,
    PredictionMarket,
    get_prediction_market_service,
)

logger = get_logger("prediction_market.router")

router = APIRouter(prefix="/api/v1/prediction-markets", tags=["prediction-markets"])


@router.get("/search")
async def search_markets(
    q: str = Query(..., description="Search query (token name, event, protocol, etc.)"),
    security_only: bool = Query(False, description="Only return security-relevant markets"),
    min_volume: float = Query(0.0, description="Minimum USD volume to include"),
    limit: int = Query(20, ge=1, le=100, description="Max results to return"),
) -> dict:
    """Search all prediction market sources for crypto security intelligence.

    Queries Polymarket, Kalshi, Limitless, and Manifold in parallel.
    Results show probability, volume, and auto-classified relevance.
    """
    svc = get_prediction_market_service()
    results = await svc.search(q, min_volume=min_volume, security_only=security_only)

    return {
        "query": q,
        "total": len(results),
        "sources_searched": ["polymarket", "kalshi", "limitless", "manifold"],
        "security_relevant": sum(1 for m in results if m.is_security_relevant),
        "crypto_relevant": sum(1 for m in results if m.is_crypto_relevant),
        "results": [_market_response(m) for m in results[:limit]],
    }


@router.get("/trending")
async def trending_markets(
    category: str | None = Query(None, description="Filter: crypto, security, regulation, all"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
) -> dict:
    """Get top trending prediction markets by volume across all sources."""
    svc = get_prediction_market_service()
    results = await svc.trending(limit=limit)

    if category and category != "all":
        if category == "security":
            results = [m for m in results if m.is_security_relevant]
        elif category == "crypto":
            results = [m for m in results if m.is_crypto_relevant]
        elif category == "regulation":
            results = [
                m
                for m in results
                if m.is_security_relevant
                and any(
                    kw in m.question.lower()
                    for kw in ["sec", "cftc", "doj", "regulation", "sanction", "ban"]
                )
            ]

    return {
        "category": category or "all",
        "total": len(results),
        "sources": ["polymarket", "kalshi", "limitless"],
        "results": [_market_response(m) for m in results[:limit]],
    }


@router.get("/token/{symbol}")
async def token_markets(
    symbol: str,
    limit: int = Query(20, ge=1, le=50, description="Max results"),
) -> dict:
    """Find all prediction markets mentioning a specific token symbol.

    Useful for: gauging market sentiment on specific tokens,
    finding rug/exploit probability markets for tokens in scanner results.
    """
    svc = get_prediction_market_service()
    results = await svc.token_markets(symbol)

    return {
        "token": symbol.upper(),
        "total": len(results),
        "avg_probability": (
            sum(m.probability_yes for m in results) / len(results) if results else 0
        ),
        "security_relevant": sum(1 for m in results if m.is_security_relevant),
        "results": [_market_response(m) for m in results[:limit]],
    }


@router.get("/sentinel")
async def sentinel_digest(
    refresh: bool = Query(False, description="Force refresh (bypass cache)"),
) -> dict:
    """Daily intelligence digest of security-relevant prediction markets.

    Categorizes markets into:
    - Top threats: High-volume security-relevant markets
    - Token-specific: Markets about specific tokens with security signals
    - Ecosystem risk: Broad crypto risk markets
    - Regulatory: SEC, CFTC, DOJ, sanction-related markets

    Used by: SENTINEL scanner for cross-referencing with on-chain risk scores.
    """
    svc = get_prediction_market_service()
    digest: PredictionDigest = await svc.security_digest()

    return {
        "generated_at": digest.generated_at,
        "summary": {
            "total_markets": digest.total_markets_searched,
            "security_relevant": digest.security_relevant_count,
            "crypto_relevant": digest.crypto_relevant_count,
        },
        "top_threats": [_market_response(m) for m in digest.top_threats],
        "token_specific": [_market_response(m) for m in digest.token_specific_markets],
        "ecosystem_risk": [_market_response(m) for m in digest.ecosystem_risk_markets],
        "regulatory": [_market_response(m) for m in digest.regulatory_markets],
    }


@router.get("/detail/{source}/{market_id}")
async def market_detail(
    source: str,
    market_id: str,
) -> dict:
    """Get detailed market data including live orderbook prices.

    Sources: polymarket, kalshi
    """
    valid_sources = {"polymarket", "kalshi"}
    if source not in valid_sources:
        raise ValidationError(f"Invalid source. Use: {', '.join(valid_sources)}")

    svc = get_prediction_market_service()
    market = await svc.market_detail(source, market_id)

    if not market:
        raise NotFoundError(f"Market not found: {source}/{market_id}")

    return {
        "market": _market_response(market),
        "interpretation": _interpret_market(market),
    }


# ── Sources Info ─────────────────────────────────────────────────


@router.get("/sources")
async def list_sources() -> dict:
    """List all integrated prediction market data sources."""
    return {
        "sources": [
            {
                "name": "Polymarket",
                "type": "Real-money prediction market (Polygon)",
                "api_count": 3,
                "endpoints": [
                    "Gamma (search/discovery)",
                    "CLOB (prices/orderbooks)",
                    "Data (trades/OI)",
                ],
                "auth": "None required for read-only",
                "rate_limits": "4K-9K req/10s",
                "url": "https://polymarket.com",
            },
            {
                "name": "Kalshi",
                "type": "Regulated prediction market (US)",
                "api_count": 1,
                "endpoints": ["REST API (series, markets, events, orderbooks)"],
                "auth": "None for market data",
                "rate_limits": "Generous",
                "url": "https://kalshi.com",
            },
            {
                "name": "Limitless Exchange",
                "type": "Daily prediction market (Base L2)",
                "api_count": 1,
                "endpoints": ["REST API (markets, orderbooks)", "WebSocket (live updates)"],
                "auth": "None for public endpoints",
                "rate_limits": "Standard",
                "url": "https://limitless.exchange",
            },
            {
                "name": "Manifold Markets",
                "type": "Play-money prediction market (sentiment signals)",
                "api_count": 1,
                "endpoints": ["REST API (search, markets, users)"],
                "auth": "None for read-only",
                "rate_limits": "Generous",
                "url": "https://manifold.markets",
            },
        ],
        "open_source_references": [
            {
                "name": "homerun",
                "repo": "braedonsaunders/homerun",
                "description": "Open-source prediction market platform for Polymarket + Kalshi. Python strategies, backtesting, live trading.",
            },
            {
                "name": "prediction-market-edge-bot",
                "description": "SX Bet + Polymarket aggregator with smart order routing.",
            },
            {
                "name": "Awesome-Prediction-Market-Tools",
                "repo": "aarora4/Awesome-Prediction-Market-Tools",
                "description": "Curated directory of 50+ tools including Oddpool, analytics, trading bots.",
            },
        ],
    }


# ── Helpers ──────────────────────────────────────────────────────


def _market_response(m: PredictionMarket) -> dict:
    """Format a PredictionMarket into API response."""
    return {
        "source": m.source,
        "question": m.question,
        "slug": m.slug,
        "probability": {
            "yes": round(m.probability_yes, 4),
            "no": round(m.probability_no, 4),
            "yes_pct": f"{m.probability_yes * 100:.1f}%",
            "no_pct": f"{m.probability_no * 100:.1f}%",
        },
        "volume_usd": round(m.volume_usd, 2),
        "category": m.category,
        "tokens_mentioned": m.tokens_mentioned,
        "is_security_relevant": m.is_security_relevant,
        "is_crypto_relevant": m.is_crypto_relevant,
        "url": m.url,
        "ends_at": m.ends_at,
        "updated_at": m.updated_at,
    }


def _interpret_market(m: PredictionMarket) -> dict:
    """Generate a human-readable interpretation of market probabilities."""
    prob_pct = m.probability_yes * 100

    if prob_pct >= 80:
        sentiment = "strong consensus"
    elif prob_pct >= 60:
        sentiment = "moderate consensus"
    elif prob_pct >= 40:
        sentiment = "divided / uncertain"
    elif prob_pct >= 20:
        sentiment = "leaning against"
    else:
        sentiment = "strong consensus against"

    signals = []
    if m.is_security_relevant:
        signals.append("security_threat_indicator")
    if m.is_crypto_relevant:
        signals.append("crypto_ecosystem_relevant")
    if m.volume_usd > 100000:
        signals.append("high_volume_confidence")
    if m.tokens_mentioned:
        signals.append(f"token_specific: {', '.join(m.tokens_mentioned[:5])}")

    return {
        "market_sentiment": sentiment,
        "probability": f"{prob_pct:.1f}%",
        "confidence": "high"
        if m.volume_usd > 100000
        else "moderate"
        if m.volume_usd > 10000
        else "low",
        "intelligence_signals": signals,
    }
