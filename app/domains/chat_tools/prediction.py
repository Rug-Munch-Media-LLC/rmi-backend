"""Prediction-markets executor for the chat tool-use layer.

"Show me interesting prediction markets" / "what are the odds on X" must
surface REAL Polymarket data (Gamma API, no key) — never a generic lecture
and never fabricated markets. Fail-open: returns ``None`` on any
error/timeout and NEVER raises.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _exec_prediction(message: str = "") -> str | None:
    """Top interesting Polymarket prediction markets, ranked. Fail-open.

    Calls the in-process ``fetch_trending_markets`` (Gamma API, no key),
    ranks by volume/liquidity/recency, and renders a compact numbered list.
    Returns None on any error/empty — chat falls through to RAG/LLM.
    """
    try:
        from app.domains.markets.prediction_markets import (
            fetch_trending_markets,
            format_market_list,
            rank_interesting,
        )

        markets = await fetch_trending_markets(limit=15)
        ranked = rank_interesting(markets)
        if not ranked:
            return None
        text = format_market_list(ranked, top_n=8)
        return text if text.strip() else None
    except Exception as e:
        logger.debug("chat_tool_prediction_failed", error=str(e)[:120])
        return None
