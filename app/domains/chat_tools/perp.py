"""Perp-markets executor for the chat tool-use layer.

"hyperliquid funding" / "show me perps" must surface REAL Hyperliquid perp
data (public info API, no key) — never a generic lecture and never
fabricated numbers. Fail-open: returns ``None`` on any error/timeout and
NEVER raises.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _exec_perp(message: str = "") -> str | None:
    """Top Hyperliquid perp markets by open interest, formatted. Fail-open.

    Calls the in-process ``fetch_hyperliquid_assets`` (public info API, no
    key), sorts by open interest, and renders a compact numbered list with
    price, funding, OI and 24h change. Returns None on any error/empty — chat
    falls through to RAG/LLM.
    """
    try:
        from app.domains.markets.perp_markets import (
            fetch_hyperliquid_assets,
            format_hyperliquid_assets,
        )

        assets = await fetch_hyperliquid_assets()
        if not assets:
            return None
        text = format_hyperliquid_assets(assets, top_n=8)
        return text if text.strip() else None
    except Exception as e:
        logger.debug("chat_tool_perp_failed", error=str(e)[:120])
        return None
