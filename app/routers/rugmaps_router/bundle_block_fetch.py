"""Bounded I/O wrapper that produces the scan-response ``clusters`` block.

Reuses the ONE bundle engine (``BundleDetector``) that /early-buyers and
/bundle-timeline already call, plus the existing wallet-label stack for wallet
age. Never raises: every failure degrades to a fully-null block that states the
reason, so a scan never fails because cluster enrichment did.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.routers.rugmaps_router.bundle_block import build_bundle_block

logger = get_logger(__name__)

# Chains the bundle engine can reach (explorer/API support). Anything else is
# reported as unsupported rather than burning the scan budget on a dead call.
_SUPPORTED_CHAINS = frozenset({"solana", "ethereum", "base", "bsc"})

_DEFAULT_TIMEOUT_S = 12.0
_FRESH_WALLET_DAYS = 7
_MAX_AGED_WALLETS = 60


def _degraded(reason: str) -> dict[str, Any]:
    """Null block carrying a specific reason instead of the generic one."""
    block = build_bundle_block(None)
    block["null_reasons"] = dict.fromkeys(block["null_reasons"], reason)
    block["confidence_basis"] = "unavailable"
    return block


async def _fresh_share(block: dict[str, Any], chain: str) -> tuple[float | None, dict[str, Any]]:
    """Share of clustered wallets younger than 7 days that had derivable age.

    Only wallets already surfaced as cluster members are aged — this is a
    property of the cluster, not of the whole holder base. Wallet age comes
    from the existing label stack; when no member has age data the share stays
    ``None`` with a sample stating how little was seen.
    """
    addresses = [w for c in (block.get("clusters") or []) for w in (c.get("wallets") or [])]
    addresses = [a for a in addresses if a][:_MAX_AGED_WALLETS]
    if not addresses:
        return None, {"clustered": 0, "with_age": 0, "threshold_days": _FRESH_WALLET_DAYS}
    try:
        from app.routers.rugmaps_router.analysis import _fetch_labels, _wallet_age_days

        labels = await _fetch_labels(addresses, chain)
        ages = [a for a in (_wallet_age_days({}, labels.get(x.lower(), {})) for x in addresses) if a is not None]
    except Exception as e:  # label stack is enrichment, never a blocker
        logger.debug(f"fresh-wallet age lookup failed for cluster members: {e}")
        ages = []
    sample = {"clustered": len(addresses), "with_age": len(ages), "threshold_days": _FRESH_WALLET_DAYS}
    if not ages:
        return None, sample
    return round(100.0 * sum(1 for a in ages if a < _FRESH_WALLET_DAYS) / len(ages), 2), sample


async def fetch_bundle_block(token_address: str, chain: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run the bundle engine (bounded) and return the normalized block.

    Never raises. Timeout, engine error, unsupported chain and an empty report
    all produce a null block with an explicit ``null_reasons`` entry.
    """
    chain_key = (chain or "").lower()
    if chain_key not in _SUPPORTED_CHAINS:
        return _degraded(f"chain_not_supported:{chain_key or 'unknown'}")

    detector = None
    try:
        from app.domains.scanners.bundle_detector import BundleDetector

        detector = BundleDetector()
        report = await asyncio.wait_for(detector.detect_bundles(token_address, chain_key), timeout=timeout_s)
    except TimeoutError:
        logger.debug(f"bundle detection timed out for {token_address[:12]}")
        return _degraded(f"bundle_detection_timeout:{timeout_s}s")
    except Exception as e:
        logger.debug(f"bundle detection failed for {token_address[:12]}: {e}")
        return _degraded(f"bundle_detection_failed:{type(e).__name__}")
    finally:
        if detector is not None:
            try:
                await detector.close()
            except Exception as e:
                logger.debug(f"bundle detector close failed: {e}")

    block = build_bundle_block(report)
    share, sample = await _fresh_share(block, chain_key)
    block["fresh_wallet_share_pct"] = share
    block["fresh_wallet_sample"] = sample
    if share is not None:
        block["null_reasons"].pop("fresh_wallet_share_pct", None)
    return block


__all__ = ["fetch_bundle_block"]
