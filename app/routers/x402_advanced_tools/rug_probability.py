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

One signal collector per module (``_signal_*``); this router only orchestrates:
sum the points, order the reasons, clamp, wrap, cache. Response shape is
unchanged — see ``_envelope``.
"""

import time
from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.x402_cache import get_cached, set_cached

# Runtime import: FastAPI resolves this body model at OpenAPI schema generation time.
from app.routers.x402_advanced_tools._envelope import EnvelopePayload, envelope, tier_for
from app.routers.x402_advanced_tools._signal_dexscreener import dexscreener_hits
from app.routers.x402_advanced_tools._signal_honeypot import honeypot_hits
from app.routers.x402_advanced_tools._signal_scam import scam_hits
from app.routers.x402_advanced_tools._signal_social import social_hits
from app.routers.x402_advanced_tools.models import AddressRequest

logger = get_logger("x402.rug_probability")

Hit = tuple[int, dict[str, Any] | None]


async def _all_hits(addr: str, chain: str) -> list[Hit]:
    """Collect every signal in reason order: honeypot, dexscreener, scam, social."""
    return [
        *await honeypot_hits(addr, chain),
        *await dexscreener_hits(addr),
        *scam_hits(addr, chain),
        *await social_hits(addr),
    ]


async def rug_probability(req: AddressRequest) -> Any:
    """Predictive rug pull probability: 0-100 score."""
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        t0 = time.time()
        cache_key = {"address": addr, "chain": chain}

        cached = get_cached("rug_probability", cache_key)
        if cached:
            return cached

        hits = await _all_hits(addr, chain)
        probability = max(0, min(100, sum(points for points, _ in hits)))
        signals = [signal for _, signal in hits if signal is not None]
        tier, recommendation = tier_for(probability)

        result = envelope(
            EnvelopePayload(
                address=addr,
                chain=chain,
                probability=probability,
                tier=tier,
                recommendation=recommendation,
                signals=signals,
                started=t0,
            )
        )

        set_cached("rug_probability", cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Rug probability failed: {e}")
        raise AppError(message=str(e)) from e


__all__ = ["rug_probability"]
