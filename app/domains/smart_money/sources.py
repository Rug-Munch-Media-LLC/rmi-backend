"""ClickHouse reads for smart-money moves (database ``wallet_memory``).

Evidence sources:
  * ``evm_transfers``              - EVM token in/out (direction is explicit)
  * ``solana_spl_balance_changes`` - Solana SPL balance deltas (signed)
  * ``token_prices``               - USD price per (chain, token)
  * ``token_meta_ref``             - symbol + decimals
  * ``wallet_labels`` / ``entity_labels`` - labels (never invented)
  * ``wallet_entities``            - cluster id (optional, guarded)

All values are passed as ClickHouse query parameters; no value is ever
interpolated into SQL text. ``wallet_profiles`` is deliberately not used: it
holds 2 rows in production, so depending on it would fake coverage.
"""
# ruff: noqa: S608 -- SQL is built from constants + bound parameters

from __future__ import annotations

import logging
import os
from typing import Any

import clickhouse_connect

from app.domains.smart_money.known_tokens import known_cte

logger = logging.getLogger(__name__)

MIN_MOVE_USD = 25_000.0
# Above this a single wallet/token aggregate is almost certainly a bad price
# in token_prices (seen: WETH on base priced at $34.6M). Reject, do not clamp.
MAX_PLAUSIBLE_MOVE_USD = 5e8
CANDIDATE_LIMIT = 400

# Addresses that are infrastructure, not traders. Excluded by entity type
# (labelled evidence) plus the two universal sentinels.
INFRA_ENTITY_TYPES = (
    "contract", "dex", "bridge", "stablecoin", "lending",
    "exchange", "malicious", "mixer", "mev_bot", "unknown",
)
SENTINELS = (
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
)

_EVM_FLOWS = (
    "WITH " + known_cte() + """,  # noqa: S608
    priced AS (
    SELECT chain, token_address, max(price_usd) AS px,
           anyIf(symbol, symbol != '') AS symbol
    FROM wallet_memory.token_prices
    WHERE price_usd > 0
    GROUP BY chain, token_address
),
meta AS (
    SELECT chain, token_address, max(decimals) AS decimals,
           anyIf(symbol, symbol != '') AS symbol
    FROM wallet_memory.token_meta_ref
    GROUP BY chain, token_address
),
flows AS (
    SELECT t.chain AS chain, t.to_address AS wallet, t.token_address AS token,
           coalesce(nullIf(p.symbol, ''), nullIf(m.symbol, ''),
                    nullIf(k.symbol, ''), '') AS symbol,
           toFloat64(toUInt256OrZero(t.value)) / pow(10, coalesce(m.decimals, k.decimals, 18)) * p.px AS usd,
           1 AS dir, t.block_timestamp AS ts
    FROM wallet_memory.evm_transfers AS t
    INNER JOIN priced AS p ON p.chain = t.chain AND p.token_address = t.token_address
    LEFT JOIN meta AS m ON m.chain = t.chain AND m.token_address = t.token_address
    LEFT JOIN known AS k ON k.chain = t.chain AND k.token_address = t.token_address
    WHERE t.block_timestamp >= now() - toIntervalHour({hours:UInt32})
      AND t.chain IN {chains:Array(String)}
    UNION ALL
    SELECT t.chain, t.from_address, t.token_address, coalesce(p.symbol, ''),
           toFloat64(toUInt256OrZero(t.value)) / pow(10, coalesce(m.decimals, k.decimals, 18)) * p.px,
           -1, t.block_timestamp
    FROM wallet_memory.evm_transfers AS t
    INNER JOIN priced AS p ON p.chain = t.chain AND p.token_address = t.token_address
    LEFT JOIN meta AS m ON m.chain = t.chain AND m.token_address = t.token_address
    LEFT JOIN known AS k ON k.chain = t.chain AND k.token_address = t.token_address
    WHERE t.block_timestamp >= now() - toIntervalHour({hours:UInt32})
      AND t.chain IN {chains:Array(String)}
)
SELECT chain, wallet, token, any(symbol) AS symbol,
       sumIf(usd, dir = 1) AS inflow_usd,
       sumIf(usd, dir = -1) AS outflow_usd,
       max(ts) AS last_ts
FROM flows
WHERE wallet NOT IN (
        SELECT address FROM wallet_memory.entity_labels
        WHERE entity_type IN {infra:Array(String)}
      )
  AND wallet NOT IN {sentinels:Array(String)}
GROUP BY chain, wallet, token
HAVING inflow_usd + outflow_usd >= {min_usd:Float64}
   AND inflow_usd + outflow_usd <= {max_usd:Float64}
ORDER BY inflow_usd + outflow_usd DESC
LIMIT {limit:UInt32}
""")


_SOLANA_FLOWS = """
WITH priced AS (
    SELECT token_address, max(price_usd) AS px,
           anyIf(symbol, symbol != '') AS symbol
    FROM wallet_memory.token_prices
    WHERE price_usd > 0 AND chain = 'solana'
    GROUP BY token_address
)
SELECT 'solana' AS chain, c.owner AS wallet, c.mint AS token,
       any(if(coalesce(c.token_symbol, '') = '', p.symbol, c.token_symbol)) AS symbol,
       sumIf(abs(toFloat64OrZero(c.delta)) / pow(10, c.decimals) * p.px,
             toFloat64OrZero(c.delta) > 0) AS inflow_usd,
       sumIf(abs(toFloat64OrZero(c.delta)) / pow(10, c.decimals) * p.px,
             toFloat64OrZero(c.delta) < 0) AS outflow_usd,
       max(c.block_timestamp) AS last_ts
FROM wallet_memory.solana_spl_balance_changes AS c
INNER JOIN priced AS p ON p.token_address = c.mint
WHERE c.block_timestamp >= now() - toIntervalHour({hours:UInt32})
  AND c.owner NOT IN (
        SELECT address FROM wallet_memory.entity_labels
        WHERE entity_type IN {infra:Array(String)}
      )
GROUP BY wallet, token
HAVING inflow_usd + outflow_usd >= {min_usd:Float64}
   AND inflow_usd + outflow_usd <= {max_usd:Float64}
ORDER BY inflow_usd + outflow_usd DESC
LIMIT {limit:UInt32}
"""

_COLS = ("chain", "wallet", "token", "symbol", "inflow_usd", "outflow_usd", "last_ts")

_WALLET_LABELS = """
SELECT lower(address) AS address, label_name FROM wallet_memory.wallet_labels
WHERE lower(address) IN {wallets:Array(String)} AND label_name != ''
GROUP BY address, label_name
"""

_ENTITY_LABELS = """
SELECT lower(address) AS address, name FROM wallet_memory.entity_labels
WHERE lower(address) IN {wallets:Array(String)} AND name != ''
GROUP BY address, name
"""

_CLUSTERS = """
SELECT lower(wallet_address) AS address, entity_id FROM wallet_memory.wallet_entities
WHERE lower(wallet_address) IN {wallets:Array(String)} AND entity_id != ''
"""

_PRICES = """
SELECT chain, token_address, max(price_usd) FROM wallet_memory.token_prices
WHERE price_usd > 0 AND chain IN {chains:Array(String)}
GROUP BY chain, token_address
"""


def get_client() -> Any:
    """Open a ClickHouse client. Raises if the server is unreachable."""
    return clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "rmi-clickhouse"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "rmi"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        connect_timeout=5,
        send_receive_timeout=30,
    )


def fetch_token_flows(client: Any, chains: list[str], hours: int) -> list[dict[str, Any]]:
    """Return per (wallet, chain, token) USD inflow/outflow aggregates."""
    rows: list[dict[str, Any]] = []
    params = {
        "hours": hours,
        "min_usd": MIN_MOVE_USD,
        "max_usd": MAX_PLAUSIBLE_MOVE_USD,
        "limit": CANDIDATE_LIMIT,
        "infra": list(INFRA_ENTITY_TYPES),
        "sentinels": list(SENTINELS),
    }
    evm = [c for c in chains if c != "solana"]
    if evm:
        res = client.query(_EVM_FLOWS, parameters={**params, "chains": evm})
        rows.extend(dict(zip(_COLS, r, strict=True)) for r in res.result_rows)
    if "solana" in chains:
        res = client.query(_SOLANA_FLOWS, parameters=params)
        rows.extend(dict(zip(_COLS, r, strict=True)) for r in res.result_rows)
    return rows


def _label_query(client: Any, sql: str, wallets: list[str], out: dict[str, list[str]]) -> None:
    try:
        for address, name in client.query(sql, parameters={"wallets": wallets}).result_rows:
            if name and name not in out.setdefault(address, []):
                out[address].append(name)
    except Exception as exc:
        logger.warning("label lookup failed: %s", exc)


def fetch_wallet_labels(client: Any, wallets: list[str]) -> dict[str, list[str]]:
    """Return ``{address: [label, ...]}`` from wallet_labels + entity_labels."""
    out: dict[str, list[str]] = {}
    if not wallets:
        return out
    _label_query(client, _WALLET_LABELS, wallets, out)
    _label_query(client, _ENTITY_LABELS, wallets, out)
    return out


def fetch_cluster_ids(client: Any, wallets: list[str]) -> dict[str, str]:
    """Return ``{address: cluster_id}`` where a cluster is materialised."""
    if not wallets:
        return {}
    try:
        rows = client.query(_CLUSTERS, parameters={"wallets": wallets}).result_rows
    except Exception as exc:
        logger.info("cluster lookup unavailable: %s", exc)
        return {}
    return {a: e for a, e in rows if a and e}


def fetch_prices(client: Any, chains: list[str]) -> dict[tuple[str, str], float]:
    """Return ``{(chain, token): price_usd}``. ``token_prices`` is tiny (~7k)."""
    if not chains:
        return {}
    try:
        rows = client.query(_PRICES, parameters={"chains": sorted(set(chains))}).result_rows
    except Exception as exc:
        logger.warning("price lookup failed: %s", exc)
        return {}
    return {(c, t): float(px) for c, t, px in rows if px}
