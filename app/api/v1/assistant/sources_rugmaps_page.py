"""RugMaps page-context builder (holders + bundle detection)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.api.v1.assistant.models import PageParams, Section
from app.api.v1.assistant.sources_rugmaps import _addr, _holders, _labels
from app.api.v1.assistant.sources_rugmaps_clusters import (
    funding_summary,
    holder_summary,
    select_cluster,
    summarize_clusters,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 15.0


async def _bundle(token: str, chain: str) -> dict[str, Any]:
    try:
        from app.domains.scanner.bundle_analysis import detect_bundle

        return await asyncio.wait_for(detect_bundle(token, chain), timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("page_context_bundle_failed: %s", e)
        return {"bundled": False, "reason": str(e)[:200]}


def _holder_sections(
    holders: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Section]:
    reason = None if holders else "no_holder_data"
    return {
        "holders": Section(
            source="rugmaps.get_token_holders",
            data=holder_summary(holders, labels) if holders else None,
            reason=reason,
        ),
        "clusters": Section(
            source="rugmaps.cluster_groups",
            data={"cluster_count": len(rows), "clusters": rows[:20]} if rows else None,
            reason=reason,
        ),
        "funding": Section(
            source="rugmaps.funding_sources",
            data=funding_summary(holders, labels) if holders else None,
            reason=reason,
        ),
    }


def _bundle_section(bundle: dict[str, Any]) -> Section:
    ok = "bundled" in bundle
    return Section(
        source="scanner.detect_bundle",
        data=bundle if ok else None,
        reason=None if ok else "bundle_unavailable",
    )


def _selected_section(selected: dict[str, Any] | None) -> Section:
    return Section(
        source="rugmaps.cluster_groups",
        data=selected,
        reason=None if selected else "cluster_not_found",
    )


async def build_rugmaps(params: PageParams) -> dict[str, Section]:
    """Build rugmaps sections — holders/clusters/funding/bundles."""
    holders, bundle = await asyncio.gather(
        _holders(params.token, params.chain, params.limit),
        _bundle(params.token, params.chain),
    )
    labels = await _labels([_addr(h) for h in holders if _addr(h)], params.chain)
    rows, _largest = summarize_clusters(holders, labels)
    return {
        **_holder_sections(holders, labels, rows),
        "bundles": _bundle_section(bundle),
        "selected_cluster": _selected_section(select_cluster(rows, params.cluster)),
    }
