"""Per-chain trending candidates: one query, one chain, one window.

The window is anchored on the chain's own newest indexed event, not on
``now()``: a stalled indexer then degrades to the last real window instead of
silently serving an empty board. ``stale_s``/``window_s`` state how old that
window is, so callers can label it instead of pretending it is live.

Every returned number comes from a table we index ourselves. Liquidity is NOT
included because ``dex_events`` stores no pool reserves (amount0/amount1 are
zero) — it is reported as unavailable rather than estimated.
"""

from __future__ import annotations

from typing import Any

from app.trending.ch_client import query_rows

PER_CHAIN_SQL = """
WITH
 (SELECT max(block_timestamp) FROM wallet_memory.dex_events WHERE chain = %(chain)s) AS hi,
 (SELECT greatest(min(block_timestamp), max(block_timestamp) - toIntervalHour(%(hours)s))
  FROM wallet_memory.dex_events WHERE chain = %(chain)s) AS lo,
 ev AS (
  SELECT token, ts, sender, volume_usd, px, is_buy,
         exp(-0.693147 * dateDiff('second', ts, hi) / greatest(dateDiff('second', lo, hi)/2.0, 60)) AS w
  FROM (
   SELECT token0 AS token, block_timestamp AS ts, sender, volume_usd, price_usd AS px,
          toUInt8(toFloat64OrZero(amount0_out) > 0) AS is_buy
   FROM wallet_memory.dex_events
   WHERE chain = %(chain)s AND block_timestamp BETWEEN lo AND hi
     AND volume_usd > 0 AND price_usd > 0
   UNION ALL
   SELECT token1, block_timestamp, sender, volume_usd, price_usd,
          toUInt8(toFloat64OrZero(amount1_out) > 0)
   FROM wallet_memory.dex_events
   WHERE chain = %(chain)s AND block_timestamp BETWEEN lo AND hi
     AND volume_usd > 0 AND price_usd > 0
  ) d
 ),
 agg AS (
  SELECT token, sum(volume_usd) AS vol, count() AS swaps, uniqExact(sender) AS traders,
    uniqExactIf(sender, is_buy = 1) AS buyers,
    sumIf(volume_usd, is_buy = 1) / greatest(sum(volume_usd), 1e-9) AS buy_share,
    argMin(px, ts) AS first_px, argMax(px, ts) AS last_px,
    min(ts) AS first_ts, max(ts) AS last_ts
  FROM ev GROUP BY token ORDER BY vol DESC LIMIT 400
 ),
 cand AS (SELECT token FROM agg),
 wv AS (SELECT token, sender, sum(volume_usd) AS v, uniqExact(is_buy) AS sides
        FROM ev WHERE token IN cand GROUP BY token, sender),
 conc AS (SELECT token, max(v) / greatest(sum(v), 1e-9) AS top1_share,
                 sumIf(v, sides = 2) / greatest(sum(v), 1e-9) AS roundtrip_share
         FROM wv GROUP BY token),
 hbd AS (SELECT token_address, address, argMax(balance, version) AS b
         FROM wallet_memory.holder_balances
         WHERE chain = %(chain)s AND token_address IN cand
         GROUP BY token_address, address),
 hb AS (SELECT token_address, sum(b) AS tot FROM hbd GROUP BY token_address),
 h10 AS (SELECT token_address, sum(b) AS top10
         FROM (SELECT * FROM hbd ORDER BY b DESC LIMIT 10 BY token_address)
         GROUP BY token_address)
SELECT a.token, any(m.symbol) AS symbol, a.vol, a.swaps, a.buyers, a.traders, a.buy_share,
  c.top1_share, c.roundtrip_share,
  if(hb.tot > 0, h10.top10 / hb.tot, NULL) AS top10_holder_share,
  a.first_px, a.last_px, a.first_ts, a.last_ts,
  dateDiff('second', a.first_ts, lo) AS age_s,
  dateDiff('second', lo, hi) AS window_s,
  dateDiff('second', a.last_ts, hi) AS stale_s
FROM agg a
ANY LEFT JOIN conc c ON c.token = a.token
ANY LEFT JOIN hb ON hb.token_address = a.token
ANY LEFT JOIN h10 ON h10.token_address = a.token
ANY LEFT JOIN wallet_memory.token_meta_ref m
  ON m.chain = %(chain)s AND m.token_address = a.token
GROUP BY a.token, a.vol, a.swaps, a.buyers, a.traders, a.buy_share,
  c.top1_share, c.roundtrip_share, hb.tot, h10.top10,
  a.first_px, a.last_px, a.first_ts, a.last_ts
ORDER BY a.vol DESC
LIMIT %(limit)s
"""

COLUMNS = (
    "token",
    "symbol",
    "vol",
    "swaps",
    "buyers",
    "traders",
    "buy_share",
    "top1_share",
    "roundtrip_share",
    "top10_holder_share",
    "first_px",
    "last_px",
    "first_ts",
    "last_ts",
    "age_s",
    "window_s",
    "stale_s",
)


def fetch_chain_rows(chain: str, hours: int, limit: int) -> list[dict[str, Any]]:
    """Return candidate rows for one chain, newest window, heaviest first."""
    params = {"chain": chain, "hours": int(hours), "limit": int(limit)}
    return [dict(zip(COLUMNS, row, strict=True)) for row in query_rows(PER_CHAIN_SQL, params)]
