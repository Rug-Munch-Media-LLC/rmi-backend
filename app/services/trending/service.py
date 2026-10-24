"""Trending orchestration: factors -> sentiment -> score -> ranked + boosted."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.logging import get_logger
from app.services.trending.boosts import fetch_boosts
from app.services.trending.clickhouse import fetch_factors
from app.services.trending.config import SCORE_FORMULA, SUPPORTED_CHAINS
from app.services.trending.scoring import score_candidates
from app.services.trending.sentiment import fetch_sentiment

logger = get_logger("trending.service")

DEFAULT_CHAIN = "ethereum"
_CANDIDATE_FACTOR = 3
_MAX_CANDIDATES = 300


def _normalize(chain: str) -> str:
    from app.domains.databus.providers.chains.lite_chain_indexer import _normalize_chain

    return _normalize_chain(chain or DEFAULT_CHAIN)


def _unsupported(chain: str, reason: str) -> dict[str, Any]:
    return {
        "chain": chain or None,
        "rank": None,
        "score": None,
        "score_components": None,
        "reasons": [reason],
        "icon_url": None,
        "source": "none",
        "data": [],
        "boosted": [],
        "count": 0,
        "boosted_count": 0,
        "supported_chains": list(SUPPORTED_CHAINS),
        "score_formula": SCORE_FORMULA,
        "reason": reason,
        "updated_at": int(time.time()),
    }


def _boost_only_items(
    boosts: dict[tuple[str, str], dict[str, Any]], seen: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    out = []
    for (chain, address), meta in boosts.items():
        if (chain, address) in seen:
            continue
        out.append(
            {
                "chain": chain,
                "token_address": address,
                "symbol": "",
                "rank": None,
                "score": None,
                "score_components": None,
                "penalties": [],
                "reasons": ["boosted token: excluded from rank", "not in on-chain candidate set (no measured factors)"],
                "icon_url": meta.get("icon"),
                "source": "dexscreener:token-boosts",
                "boosted": True,
                "excluded_from_rank": True,
                "boost": {"amount": meta.get("amount"), "total_amount": meta.get("total_amount")},
            }
        )
    return out


async def trending_scored(chain: str = "", limit: int = 50, hours: int = 24) -> dict[str, Any]:
    """Multi-factor trending for one chain. Unknown chain -> null score + reason."""
    ch = _normalize(chain)
    if ch not in SUPPORTED_CHAINS:
        return _unsupported(ch, f"unsupported_chain: '{ch}' is not indexed by our ClickHouse")

    candidates = min(_MAX_CANDIDATES, max(limit * _CANDIDATE_FACTOR, limit))
    rows = await asyncio.to_thread(fetch_factors, ch, hours, candidates)
    boosts = await fetch_boosts(ch)
    if not rows:
        result = _unsupported(ch, "no_recent_data: no measured candidates in the window")
        result["chain"] = ch
        result["boosted"] = _boost_only_items(boosts, set())
        result["boosted_count"] = len(result["boosted"])
        return result

    top = sorted(rows, key=lambda r: int(r.get("transfers_24h") or r.get("launch_signal") or 0), reverse=True)[:limit]
    sentiment = await fetch_sentiment([str(r.get("symbol") or "") for r in top])
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol in sentiment:
            row["social"] = sentiment[symbol]
            row["sources"] = [*row.get("sources", []), "social"]

    boosted_keys = set(boosts)
    ranked, boosted_scored = score_candidates(rows, boosted_keys, boosts)
    seen = {(r["chain"], r["token_address"]) for r in rows}
    boosted = boosted_scored + _boost_only_items(boosts, seen)
    ranked = ranked[:limit]
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    return {
        "chain": ch,
        "data": ranked,
        "boosted": boosted,
        "count": len(ranked),
        "boosted_count": len(boosted),
        "source": "own_clickhouse:multifactor",
        "score_formula": SCORE_FORMULA,
        "window_hours": hours,
        "reason": None,
        "supported_chains": list(SUPPORTED_CHAINS),
        "updated_at": int(time.time()),
    }
