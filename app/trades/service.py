"""Source orchestration: one call, per-chain fallback, real trades only.

DexScreener is deliberately absent: it publishes no public trades endpoint. Its
``/token-pairs/v1`` and ``/latest/dex/tokens`` responses carry 24h AGGREGATES
(volume, buy/sell counts) and a single price — deriving trades from those is
fabrication, which this service must never do.

A source that errors, is unkeyed, or returns no rows is skipped. If every
source yields nothing the caller gets an empty list plus the list of sources
that were tried — never a synthetic trade.

Wiring is injected as one ``TradeDeps`` value (source registry + cache store),
so callers and tests substitute behaviour without patching module attributes.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.trades.contracts import TradeDeps, TradeResult
from app.trades.gt_trades import dedupe, mark_whales, serialize
from app.trades.schema import Trade, canon_chain
from app.trades.sources import SourceMap

__all__ = ["TradeDeps", "TradeResult", "get_token_trades"]

logger = get_logger(__name__)

WHALE_USD = 10_000.0


async def get_token_trades(
    *,
    chain: str,
    token: str,
    limit: int = 100,
    min_usd: float = 0.0,
    deps: TradeDeps | None = None,
) -> TradeResult:
    """Recent real trades for a token, cached, deduped and whale-flagged."""
    network = canon_chain(chain)
    token = (token or "").strip()
    if not token:
        return TradeResult()

    deps = deps if deps is not None else TradeDeps.default()
    hit = await deps.store.read(network, token)
    if hit is not None:
        return TradeResult(
            trades=_from_cache(hit, min_usd, limit),
            source=str(hit.get("source") or "cache"),
            cached=True,
            tried=list(hit.get("sources_tried") or []),
        )

    result = await _fetch_chain(network, token, limit, deps.sources)
    result.trades = mark_whales(result.trades, WHALE_USD)
    if result.trades:
        await deps.store.write(
            network,
            token,
            {
                "trades": serialize(result.trades),
                "source": result.source,
                "sources_tried": result.tried,
            },
        )
    return TradeResult(
        trades=_filter(result.trades, min_usd, limit),
        source=result.source,
        cached=False,
        tried=result.tried,
    )


async def _fetch_chain(network: str, token: str, limit: int, sources: SourceMap) -> TradeResult:
    out = TradeResult()
    if network not in sources:
        out.tried = ["unsupported_chain"]
        return out

    for label, fetch in sources[network]:
        out.tried.append(label)
        try:
            rows = await fetch(network, token, limit)
        except Exception as exc:  # a broken source must not break the tape
            logger.warning("trade_source_exception", source=label, error=type(exc).__name__)
            continue
        if rows:
            out.trades = dedupe(rows)[:limit]
            out.source = label
            return out
    out.source = "none"
    return out


def _from_cache(payload: dict[str, Any], min_usd: float, limit: int) -> list[Trade]:
    try:
        trades = [Trade(**row) for row in payload.get("trades") or []]
    except (TypeError, ValueError) as exc:
        logger.debug("trades_cache_parse_failed", error=type(exc).__name__)
        return []
    return _filter(trades, min_usd, limit)


def _filter(trades: list[Trade], min_usd: float, limit: int) -> list[Trade]:
    if min_usd > 0:
        trades = [t for t in trades if t.amount_usd >= min_usd]
    return trades[:limit]
