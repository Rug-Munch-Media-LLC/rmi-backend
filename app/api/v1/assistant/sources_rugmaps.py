"""RugMaps page context data accessors: holders, labels."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 15.0


async def _holders(token: str, chain: str, limit: int) -> list[dict[str, Any]]:
    try:
        from app.rugmaps_ai import get_token_holders

        nodes = await asyncio.wait_for(get_token_holders(token, chain), timeout=_TIMEOUT)
        return [n for n in nodes if isinstance(n, dict)][:limit]
    except Exception as e:
        logger.warning("page_context_holders_failed: %s", e)
        return []


async def _labels(addresses: list[str], chain: str) -> dict[str, dict[str, Any]]:
    if not addresses:
        return {}
    try:
        from app.rugmaps_wallet import batch_lookup_wallet_labels

        res = await asyncio.wait_for(batch_lookup_wallet_labels(addresses, chain=chain), timeout=_TIMEOUT)
        return (res.get("labels") or {}) if isinstance(res, dict) else {}
    except Exception as e:
        logger.debug("page_context_labels_failed: %s", e)
        return {}


def _addr(node: dict[str, Any]) -> str:
    for key in ("address", "wallet", "holder", "id"):
        if node.get(key):
            return str(node[key])
    return ""


def _num(node: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = node.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _pct(node: dict[str, Any]) -> float:
    return _num(node, ("percentage", "pct", "share", "percent"))


def _cluster_fields(node: dict[str, Any]) -> str:
    for key in ("cluster_id", "cluster", "funding_cluster"):
        if node.get(key):
            return str(node[key])
    return ""
