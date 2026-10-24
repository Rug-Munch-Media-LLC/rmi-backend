"""Scanner page context: verdict, risk reasons, bundle/cluster block."""

from __future__ import annotations

import asyncio
from typing import Any

from app.api.v1.assistant.models import PageParams, Section
from app.api.v1.assistant.sources_rugmaps import _addr, _holders, _labels
from app.api.v1.assistant.sources_rugmaps_clusters import select_cluster, summarize_clusters
from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 25.0


def _verdict(result: Any) -> dict[str, Any]:
    safety = int(getattr(result, "safety_score", 50) or 50)
    risk_score = max(0, min(100, 100 - safety))
    if risk_score >= 70:
        level = "critical"
    elif risk_score >= 40:
        level = "high"
    elif safety >= 70:
        level = "low"
    else:
        level = "medium"
    return {
        "risk_score": risk_score,
        "risk_level": level,
        "safety_score": safety,
        "confidence": int(getattr(result, "confidence", 0) or 0),
        "symbol": getattr(result, "symbol", "") or None,
        "name": getattr(result, "name", "") or None,
        "scanned_at": getattr(result, "scanned_at", None),
        "modules_run": [
            {"module": m.get("module"), "status": m.get("status")} for m in (getattr(result, "modules_run", None) or [])
        ],
        "source": "scanner.service.scan_token:free",
    }


async def _scan(token: str, chain: str) -> Any | None:
    from app.domains.scanner.service import scan_token as sentinel_scan

    try:
        return await asyncio.wait_for(
            sentinel_scan(token_address=token, chain=chain, tiers=["free"]),
            timeout=_TIMEOUT,
        )
    except Exception as e:
        logger.warning("page_context_scan_failed: %s", e)
        return None


def _scan_sections(result: Any) -> dict[str, Section]:
    if result is None:
        return {
            "verdict": Section(source="scanner.service.scan_token:free", reason="scan_unavailable"),
            "risk_reasons": Section(source="scanner.risk_flags", reason="scan_unavailable"),
            "bundle_block": Section(source="scanner.bundle_analysis", reason="scan_unavailable"),
        }
    flags = list(getattr(result, "risk_flags", None) or [])
    return {
        "verdict": Section(source="scanner.service.scan_token:free", data=_verdict(result)),
        "risk_reasons": Section(
            source="scanner.risk_flags",
            data={"flags": flags, "flag_count": len(flags)},
        ),
        "bundle_block": Section(
            source="scanner.bundle_analysis",
            data={"bundled": "bundle" in flags or "bundled_launch" in flags, "flag_source": "scanner.risk_flags"},
        ),
    }


async def build_scanner(params: PageParams) -> dict[str, Section]:
    """Build scanner sections from the SENTINEL free tier + bundle/cluster probes."""
    result = await _scan(params.token, params.chain)
    holders = await _holders(params.token, params.chain, params.limit)
    labels = await _labels([_addr(h) for h in holders if _addr(h)], params.chain)
    rows, _largest = summarize_clusters(holders, labels)
    selected = select_cluster(rows, params.cluster)
    return {
        **_scan_sections(result),
        "cluster_block": Section(
            source="rugmaps.cluster_groups",
            data={"cluster_count": len(rows), "selected_cluster": selected} if rows else None,
            reason=None if rows else "no_holder_data",
        ),
    }
