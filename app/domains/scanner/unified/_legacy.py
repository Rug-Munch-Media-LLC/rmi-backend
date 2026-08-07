"""Backend-compat wrappers for the unified token scanner.

Split out of ``scanner.py`` (degodding 2026-08-07). These provide the legacy
``scan_token``-compatible surface (tier lists, plain-dict results) on top of
the unified ``UnifiedTokenScanner`` so queue workers and legacy callers keep
working unchanged. Public entry point: ``scan_token_unified``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import DeepScanProfile, ScanResult

logger = get_logger(__name__)

# ── Backend-compat wrapper ───────────────────────────────────────────
# Legacy tier → unified phase mapping.  ``UnifiedTokenScanner.scan`` gates
# its PREMIUM phase on ``tier`` (elite/admin); CORE + DEEP + INTEL are
# always run as free enrichment, so the legacy tiers map onto the unified
# phase set through the ``tier`` parameter:
#   free  → CORE  (+ DEEP/INTEL free enrichment)
#   pro   → CORE + DEEP
#   elite → CORE + DEEP + PREMIUM
_LEGACY_TIER_ORDER: dict[str, int] = {"free": 0, "pro": 1, "elite": 2}


def _resolve_tier(tier: str, tiers: Any = None) -> str:
    """Resolve a legacy tier list (or single tier) to a unified tier string.

    The legacy ``scan_token`` accepted ``tiers=[...]`` while the unified
    scanner takes a single ``tier``.  When a list is passed, the highest
    tier wins (free < pro < elite).
    """
    if isinstance(tiers, (list, tuple)) and tiers:
        valid = [str(t).lower() for t in tiers if str(t).lower() in _LEGACY_TIER_ORDER]
        if valid:
            tier = max(valid, key=lambda t: _LEGACY_TIER_ORDER[t])
    tier = str(tier or "free").lower()
    return tier if tier in _LEGACY_TIER_ORDER else "free"


def _to_legacy_dict(result: ScanResult) -> dict[str, Any]:
    """Convert a unified ``ScanResult`` to the legacy dict shape worker.py expects."""
    safety_score = float(result.safety_score)
    modules_run = [{"module": m, "status": "success"} for m in result.modules_run]
    return {
        "token_address": result.token_address,
        "chain": result.chain,
        "scanned_at": result.scanned_at,
        "name": result.name,
        "symbol": result.symbol,
        "status": "completed",
        "safety_score": safety_score,
        "risk_score": round(100 - safety_score, 2),
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "flags": result.risk_flags,
        "green_flags": result.green_flags,
        "breakdown": result.breakdown,
        "evidence": result.evidence,
        "modules_ran": len(result.modules_run),
        "modules_successful": len(result.modules_run),
        "modules_run": modules_run,
        "tier": result.tier,
        "scan_tier": result.scan_tier,
        "deep_profile": _deep_profile_to_dict(result.deep_profile),
        "price_usd": result.price_usd,
        "market_cap_usd": result.market_cap_usd,
        "liquidity_usd": result.liquidity_usd,
        "volume_24h_usd": result.volume_24h_usd,
        "age_hours": result.age_hours,
        "enrichment_sources": result.enrichment_sources,
        "scan_duration_ms": result.scan_duration_ms,
        "funding_analysis": result.funding_analysis,
        "wallet_freshness": result.wallet_freshness,
        "holder_overlap": result.holder_overlap,
        "news_sentiment": result.news_sentiment,
        "news_card": result.news_card,
        "event_correlation": result.event_correlation,
        "sentinel": result.sentinel,
        "sentinel_boost": result.sentinel_boost,
        "market_context": result.market_context.to_dict() if result.market_context else None,
    }


def _deep_profile_to_dict(profile: DeepScanProfile | None) -> dict[str, Any] | None:
    """Serialize a ``DeepScanProfile`` to a plain JSON-able dict (or None)."""
    if profile is None:
        return None
    return asdict(profile)


async def scan_token_unified(
    token_address: str,
    chain: str = "ethereum",
    tier: str = "free",
    **kwargs: Any,
) -> dict[str, Any]:
    """Backend-compat wrapper: same signature family as the legacy ``scan_token``.

    Routes through the cached ``UnifiedTokenScanner`` (the same instance the
    v2 API uses) and returns a plain dict shaped like the legacy
    ``ScanResult`` so queue workers keep working unchanged.

    Tier → phase mapping is handled by ``UnifiedTokenScanner.scan``:
      free  → CORE
      pro   → CORE + DEEP
      elite → CORE + DEEP + PREMIUM
    (DEEP/INTEL also run as free enrichment in the unified scanner.)

    Legacy ``tiers=[...]`` lists are accepted via ``**kwargs``; the highest
    tier wins.
    """
    from app.domains.scanner.unified import get_token_scanner

    scanner = get_token_scanner()
    resolved_tier = _resolve_tier(tier, kwargs.get("tiers"))
    result = await scanner.scan(address=token_address, chain=chain, tier=resolved_tier)
    logger.info(
        "scan_token_unified_complete",
        token=token_address[:16],
        chain=chain,
        tier=resolved_tier,
        safety_score=result.safety_score,
        risk_flags=len(result.risk_flags),
    )
    return _to_legacy_dict(result)
