"""Per-token aggregation over ``wallet_memory.dex_events``.

``price_usd`` in dex_events is the USD price of ``token0``. For the token1
side the price is derived as ``volume_usd / token_amount`` using the token's
decimals from ``token_meta_ref``; when decimals are unknown the price stays
NULL instead of being estimated.
"""

from __future__ import annotations

from typing import Any

from app.trending.ch_client import query_rows

DEX_SQL = """
WITH ev AS (
    SELECT chain AS chain, block_timestamp, sender, volume_usd, price_usd,
           token0 AS token, 1 AS tok0,
           toFloat64OrZero(amount0_in) AS amt_in,
           toFloat64OrZero(amount0_out) AS amt_out
    FROM wallet_memory.dex_events
    WHERE block_timestamp >= now() - toIntervalHour(%(hours)s)
      AND price_usd > 0 AND volume_usd > 0
      AND (%(chain)s = '' OR chain = %(chain)s)
    UNION ALL
    SELECT chain AS chain, block_timestamp, sender, volume_usd, price_usd,
           token1 AS token, 0 AS tok0,
           toFloat64OrZero(amount1_in) AS amt_in,
           toFloat64OrZero(amount1_out) AS amt_out
    FROM wallet_memory.dex_events
    WHERE block_timestamp >= now() - toIntervalHour(%(hours)s)
      AND price_usd > 0 AND volume_usd > 0
      AND (%(chain)s = '' OR chain = %(chain)s)
),
norm AS (
    SELECT ev.chain AS chain, ev.token AS token, ev.block_timestamp AS block_timestamp,
           ev.sender AS sender, ev.volume_usd AS volume_usd, ev.price_usd AS price_usd,
           ev.tok0 AS tok0, ev.amt_in AS amt_in, ev.amt_out AS amt_out,
           m.decimals AS decimals, m.symbol AS symbol, m.name AS name
    FROM ev
    ANY LEFT JOIN wallet_memory.token_meta_ref m
      ON m.chain = ev.chain AND m.token_address = ev.token
    WHERE ev.amt_in + ev.amt_out > 0
),
priced AS (
    SELECT chain, token, block_timestamp, sender, volume_usd, symbol, name,
           amt_out, decimals,
           if(tok0 = 1, price_usd,
              if(decimals IS NOT NULL AND decimals <= 36 AND greatest(amt_in, amt_out) > 0,
                 volume_usd / (greatest(amt_in, amt_out) / pow(10, decimals)), NULL)) AS px
    FROM norm
)
SELECT chain, token,
       any(symbol) AS symbol,
       any(name) AS name,
       argMin(px, block_timestamp) AS first_px,
       argMax(px, block_timestamp) AS last_px,
       sum(volume_usd) AS volume_usd,
       count() AS trades,
       countIf(amt_out > 0) AS buys,
       uniqExact(sender) AS traders,
       max(block_timestamp) AS last_ts
FROM priced
GROUP BY chain, token
ORDER BY volume_usd DESC
LIMIT %(limit)s
"""


def fetch_dex_rows(chain: str, hours: int, limit: int) -> list[dict[str, Any]]:
    """Return aggregated per-token rows for the window, newest activity first."""
    params = {"hours": int(hours), "chain": chain or "", "limit": int(limit)}
    keys = (
        "chain",
        "token",
        "symbol",
        "name",
        "first_px",
        "last_px",
        "volume_usd",
        "trades",
        "buys",
        "traders",
        "last_ts",
    )
    return [dict(zip(keys, row, strict=True)) for row in query_rows(DEX_SQL, params)]
