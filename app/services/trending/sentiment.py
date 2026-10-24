"""Social sentiment via RugBrain news retrieval (lexicon polarity).

Method (deliberately dumb, deliberately documented): retrieve RugBrain news
snippets matching the symbol, count them as ``mentions`` and score polarity
with a fixed crypto lexicon. No LLM, no invented score. No hits -> ``None``
so the scorer omits the factor instead of guessing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger("trending.sentiment")

_POS = (
    "surge",
    "rally",
    "breakout",
    "partnership",
    "bullish",
    "adoption",
    "burn",
    "gain",
    "record",
    "upgrade",
    "listing",
)
_NEG = ("hack", "exploit", "rug", "dump", "scam", "crash", "bearish", "delist", "lawsuit", "drop", "plunge", "fraud")
_MAX_SYMBOLS = 12
_METHOD = "lexicon_over_rugbrain_news"


def _polarity(text: str) -> float:
    low = text.lower()
    pos = sum(low.count(word) for word in _POS)
    neg = sum(low.count(word) for word in _NEG)
    return 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)


async def _one(symbol: str) -> dict[str, Any] | None:
    if not symbol:
        return None
    from app.integrations.rugbrain_client import rugbrain_search

    try:
        hits = await rugbrain_search(query=f"{symbol} token", collections=["news_v2"], limit=6)
    except Exception as exc:  # retrieval is best-effort, never fatal
        logger.debug("rugbrain sentiment failed for %s: %s", symbol, exc)
        return None
    if not hits:
        return None
    texts = []
    for hit in hits:
        payload = hit.get("payload") if isinstance(hit, dict) else None
        if isinstance(payload, dict):
            texts.append(" ".join(str(payload.get(k) or "") for k in ("title", "summary", "text")))
        elif isinstance(hit, dict):
            texts.append(str(hit.get("text") or ""))
    joins = " ".join(texts)
    if not joins.strip():
        return None
    return {"mentions": len(hits), "polarity": round(_polarity(joins), 3), "method": _METHOD}


async def fetch_sentiment(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Symbol -> sentiment for at most ``_MAX_SYMBOLS`` unique symbols."""
    unique = list(dict.fromkeys(s for s in symbols if s))[:_MAX_SYMBOLS]
    if not unique:
        return {}
    results = await asyncio.gather(*(_one(s) for s in unique), return_exceptions=True)
    return {sym: res for sym, res in zip(unique, results, strict=False) if isinstance(res, dict)}
