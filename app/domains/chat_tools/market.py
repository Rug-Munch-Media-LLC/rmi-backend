"""Market executor for the chat tool-use layer.

Live BTC/ETH/SOL snapshot + fear & greed (reuses chat's market fetch).
Fail-open: returns ``None`` on any error/timeout and NEVER raises.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

from ._shared import _to_float

logger = get_logger(__name__)


async def _fetch_fear_greed() -> str | None:
    """Fear & Greed index from alternative.me (free, no key). Best-effort."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            if r.status_code != 200:
                return None
            row = r.json().get("data", [{}])[0]
            value = row.get("value")
            if not value:
                return None
            label = row.get("value_classification", "")
            return f"{value}/100{(' (' + label + ')') if label else ''}"
    except Exception:
        return None


async def _exec_market(message: str = "") -> str | None:
    """Live BTC/ETH/SOL snapshot + fear & greed (reuses chat's market fetch)."""
    # _fetch_fear_greed is patch-mocked by tests on app.domains.chat_tools.* —
    # resolve it through the package shim at call time (lazy to avoid an
    # import cycle — the shim imports this module).
    from app.domains.chat_tools import _fetch_fear_greed

    try:
        from app.routers.chat import _fetch_market_context

        ctx = await _fetch_market_context()
    except Exception:
        ctx = {}
    parts: list[str] = []
    for key, label in (("btc", "BTC"), ("eth", "ETH"), ("sol", "SOL")):
        price = _to_float(ctx.get(f"{key}_price"))
        if not price:
            continue
        change = _to_float(ctx.get(f"{key}_change_24h"))
        change_str = f" ({change:+.1f}%)" if change is not None else ""
        parts.append(f"{label} ${price:,.2f}{change_str}")
    if not parts:
        return None
    text = " | ".join(parts)
    try:
        fng = await _fetch_fear_greed()
        if fng:
            text += f" | Fear & Greed: {fng}"
    except Exception as e:
        logger.debug("chat_tool_fng_failed", error=str(e)[:120])
    return f"Market snapshot: {text}"
