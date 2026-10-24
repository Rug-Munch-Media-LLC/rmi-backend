"""Source orchestration: one call, per-chain fallback, real trades only.

Order is per chain because the sources are per chain:
  * solana        -> geckoterminal -> solanatracker -> birdeye -> gmgn
  * eth/base/...  -> geckoterminal -> birdeye -> gmgn

DexScreener is deliberately absent: it publishes no public trades endpoint. Its
``/token-pairs/v1`` and ``/latest/dex/tokens`` responses carry 24h AGGREGATES
(volume, buy/sell counts) and a single price — deriving trades from those is
fabrication, which this service must never do.

A source that errors, is unkeyed, or returns no rows is skipped. If every
source yields nothing the caller gets an empty list plus the list of sources
that were tried — never a synthetic trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.trades import cache
from app.trades.birdeye import fetch_birdeye_trades
from app.trades.gmgn import fetch_gmgn_trades
from app.trades.gt_pools import resolve_pool
from app.trades.gt_trades import dedupe, fetch_pool_trades, mark_whales, serialize
from app.trades.schema import Trade, canon_chain
from app.trades.solanatracker import fetch_solanatracker_trades

logger = get_logger(__name__)

WHALE_USD = 10_000.0


@dataclass
class TradeResult:
    trades: list[Trade] = field(default_factory=list)
    source: str = "none"
    cached: bool = False
    tried: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trades": serialize(self.trades),
            "source": self.source,
            "cached": self.cached,
            "sources_tried": self.tried,
        }


async def get_token_trades(
    *,
    chain: str,
    token: str,
    limit: int = 100,
    min_usd: float = 0.0,
) -> TradeResult:
    """Recent real trades for a token, cached, deduped and whale-flagged."""
    network = canon_chain(chain)
    token = (token or "").strip()
    if not token:
        return TradeResult()

    hit = await cache.read(network, token)
    if hit is not None:
        trades = _from_cache(hit, min_usd, limit)
        return TradeResult(
            trades=trades,
            source=str(hit.get("source") or "cache"),
            cached=True,
            tried=list(hit.get("sources_tried") or []),
        )

    result = await _fetch_chain(network, token, limit)
    result.trades = mark_whales(result.trades, WHALE_USD)
    if result.trades:
        await cache.write(
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


async def _fetch_chain(network: str, token: str, limit: int) -> TradeResult:
    out = TradeResult()
    if network not in _FALLBACKS:
        out.tried = ["unsupported_chain"]
        return out

    for label, func_name in _FALLBACKS[network]:
        out.tried.append(label)
        # Resolve through module globals so a swapped source is always honoured
        # (a captured function object would pin the import-time binding).
        fetch = globals()[func_name]
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


async def _gt(network: str, token: str, limit: int) -> list[Trade]:
    ref = await resolve_pool(network, token)
    if ref is None:
        return []
    return await fetch_pool_trades(
        chain=network,
        pool=ref.pool,
        token=token,
        base_token=ref.base_token,
        limit=limit,
    )


async def _st(network: str, token: str, limit: int) -> list[Trade]:
    return await fetch_solanatracker_trades(chain=network, token=token, limit=limit)


async def _be(network: str, token: str, limit: int) -> list[Trade]:
    return await fetch_birdeye_trades(chain=network, token=token, limit=limit)


async def _gm(network: str, token: str, limit: int) -> list[Trade]:
    return await fetch_gmgn_trades(chain=network, token=token, limit=limit)


_FALLBACKS: dict[str, list[tuple[str, str]]] = {
    "solana": [
        ("geckoterminal", "_gt"),
        ("solanatracker", "_st"),
        ("birdeye", "_be"),
        ("gmgn", "_gm"),
    ],
    **{
        net: [("geckoterminal", "_gt"), ("birdeye", "_be"), ("gmgn", "_gm")]
        for net in ("eth", "base", "bsc", "arbitrum", "polygon", "optimism", "avalanche")
    },
}


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
