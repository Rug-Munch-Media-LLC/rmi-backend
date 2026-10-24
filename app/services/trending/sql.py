"""SQL for trending factor extraction (all values bound, no interpolation)."""

from __future__ import annotations

# Candidate universe + activity/manipulation basis: real swaps from dex_events.
_MARKET_SQL = """
SELECT tok, sum(c) AS swaps, uniqExact(sender) AS senders,
       max(c) AS top_swaps, sum(vol) AS volume_usd
FROM (
    SELECT if(token0 IN %(quotes)s, token1, token0) AS tok, sender,
           count() AS c, sumIf(volume_usd, volume_usd > 0) AS vol
    FROM wallet_memory.dex_events
    WHERE chain = %(chain)s AND type = 'swap'
      AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
      AND sender != ''
    GROUP BY tok, sender
)
WHERE tok != ''
GROUP BY tok
ORDER BY swaps DESC
LIMIT %(limit)s
"""

# First/last observed swap price per token -> scale-free momentum.
_PRICE_MOVE_SQL = """
SELECT tok, argMin(p, t) AS p_open, argMax(p, t) AS p_last
FROM (
    SELECT if(token0 IN %(quotes)s, token1, token0) AS tok, price_usd AS p,
           block_timestamp AS t
    FROM wallet_memory.dex_events
    WHERE chain = %(chain)s AND type = 'swap'
      AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
      AND price_usd > 0
)
WHERE tok IN %(tokens)s
GROUP BY tok
"""

# Coordinated same-block buys (>=5 distinct senders) = bundle signature.
_BUNDLE_SQL = """
SELECT tok, count() AS bundled_blocks FROM (
    SELECT if(token0 IN %(quotes)s, token1, token0) AS tok, block_number,
           uniqExact(sender) AS s
    FROM wallet_memory.dex_events
    WHERE chain = %(chain)s AND type = 'swap'
      AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
      AND sender != ''
    GROUP BY tok, block_number
    HAVING s >= 5
)
WHERE tok IN %(tokens)s
GROUP BY tok
"""

# Transfer/holder activity (EVM only — the Solana balance table is stale).
_ACTIVITY_SQL = """
SELECT token_address,
       countIf(block_timestamp >= now() - INTERVAL %(hours)s HOUR) AS t_now,
       countIf(block_timestamp <  now() - INTERVAL %(hours)s HOUR) AS t_prev,
       uniqExactIf(to_address, block_timestamp >= now() - INTERVAL %(hours)s HOUR) AS h_now,
       uniqExactIf(to_address, block_timestamp <  now() - INTERVAL %(hours)s HOUR) AS h_prev
FROM wallet_memory.evm_transfers
WHERE chain = %(chain)s
  AND block_timestamp >= now() - INTERVAL %(window)s HOUR
  AND token_address IN %(tokens)s
  AND to_address != '0x0000000000000000000000000000000000000000'
GROUP BY token_address
"""

_LAUNCH_SQL = """
SELECT token0, min(block_timestamp) AS launched_at
FROM wallet_memory.evm_launches
WHERE chain = %(chain)s AND token0 IN %(tokens)s
GROUP BY token0
"""

_SYMBOL_SQL = """
SELECT token_address, argMax(symbol, last_updated) AS symbol
FROM wallet_memory.token_meta_ref
WHERE chain = %(chain)s AND token_address IN %(tokens)s
GROUP BY token_address
"""

_SOLANA_SQL = """
SELECT mint, venue, max(launch_signal) AS launch_signal,
       max(graduation_signal) AS graduation_signal, min(block_timestamp) AS launched_at
FROM wallet_memory.solana_launches
WHERE block_timestamp >= now() - INTERVAL %(window)s HOUR
GROUP BY mint, venue
ORDER BY launch_signal DESC, launched_at DESC
LIMIT %(limit)s
"""
