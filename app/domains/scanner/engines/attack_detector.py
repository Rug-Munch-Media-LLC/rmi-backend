"""
C6 — Time-Based Attack Detection Engine
==========================================
Pure ClickHouse-powered detection of sandwich attacks, pump & dumps, and
flash loans built PURELY from our own `wallet_memory.dex_events` (59M+ rows).
No external APIs.

Detectors (all read only our own dex_events):
  1. Sandwich      — attacker buy + victim swap + attacker sell in the SAME
                     block on the SAME pair, with the same sender on the
                     front-run and back-run legs.
  2. Pump & dump   — 4h volume > 10x trailing-24h baseline, price spike
                     > 100% from baseline, then drawdown > 50% from peak
                     within 24h (per-pair, from reserve-ratio price series).
  3. Flash loan    — v1 pattern-based: same tx_hash with >= 4 swap events
                     where a single pair's total in ≈ total out (repay) and
                     the target swap sits in between.

Wiring (after detection):
  - Persist events to `wallet_memory.attack_events` (MergeTree)
  - Push `attack_alert:{chain}:{token}` to Redis for real-time consumers
  - Send Telegram scanner alert when confidence == HIGH
  - Mark attacker wallets as whale in dex_events (ALTER TABLE UPDATE)

Scheduling:
  - Run as a periodic job every 5 minutes scanning the last hour of blocks
    (see run_periodic_detection / scripts/run_attack_detection.py).
  - Every ClickHouse query runs with `max_execution_time = 30`.
  - Results cached in Redis (TTL 15m) to avoid re-scanning the same blocks.

Usage:
    from app.domains.scanner.engines.attack_detector import run_attack_detection

    result = await run_attack_detection(chain="all", hours_back=1, notify=True)
    # {
    #   "result": "scanned",
    #   "chains_scanned": ["ethereum", ...],
    #   "events": [...],
    #   "events_new": 3,
    #   "inserted": 3,
    #   "alerts_pushed": 2,
    #   "telegram_sent": 1,
    #   "whale_marked": 1,
    #   ...
    # }

NOTE (degodding 2026-08-07): 1,202 lines. Split is deferred (all detectors share the
same window-query scaffolding over wallet_memory.dex_events). If splitting, move each
detector (sandwich / pump-dump / flash loan) into
``engines/attack_detector/_detectors.py`` with a common window-query helper.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.exceptions import RedisError

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.core.logging import get_logger
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from app.domains.scanner.engines.own_dex_data.format import _normalize_amount
from app.domains.scanner.engines.own_dex_data.query import get_token_price_simple

logger = get_logger("attack_detector")

# ── Constants ────────────────────────────────────────────────────────────────
_CH_DB = "wallet_memory"
DEX_EVENTS_TABLE = f"{_CH_DB}.dex_events"
ATTACK_TABLE = f"{_CH_DB}.attack_events"
MAX_EXECUTION_TIME = 30  # seconds — applied to every ClickHouse query
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes — avoid re-scanning the same blocks
MAX_EVENTS_PER_SCAN = 100  # cap on events per detector per scan

# Detection thresholds
SANDWICH_BALANCED_RATIO = 0.30  # |buy-sell|/max < 0.30 → balanced sandwich
PND_VOLUME_SPIKE_MULT = 10.0  # 4h volume > 10x trailing-24h baseline
PND_PRICE_SPIKE_PCT = 100.0  # price increase > 100% from baseline
PND_DRAWDOWN_PCT = 50.0  # drawdown > 50% from peak within 24h
PND_MIN_PAIR_SWAPS = 10  # ignore dust pairs
FLASH_REPAY_TOLERANCE = 0.10  # |in - out|/max(in,out) <= 10% → repay

# Supported chains (matches own_dex_data chain map + solana)
_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "solana": "solana",
}
ALL_CHAINS: tuple[str, ...] = (
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "base",
    "avalanche",
    "fantom",
    "gnosis",
    "solana",
)


def _normalize_chain(chain: str) -> str:
    """Normalize a chain name to the format used in ClickHouse tables."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ── Lazy clients ─────────────────────────────────────────────────────────────
_ch_client: Any = None
_redis_client: Any = None

# In-process caches
_RESULT_CACHE: dict[str, dict[str, Any]] = {}
_RESULT_CACHE_MAX = 100
_USD_PRICE_CACHE: dict[str, float] = {}
_USD_PRICE_CACHE_MAX = 200


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for the wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=35,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Attack detector ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Attack detector ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


async def _get_redis() -> Any | None:
    """Lazy-init async Redis client. Returns None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        redis_url = os.getenv("REDIS_URL", "")
        if redis_url and "://" in redis_url:
            url = redis_url
        else:
            r_host = os.getenv("REDIS_HOST", "rmi-redis")
            r_port = os.getenv("REDIS_PORT", "6379")
            r_db = os.getenv("REDIS_DB", "0")
            r_pass = os.getenv("REDIS_PASSWORD", "")
            url = f"redis://:{r_pass}@{r_host}:{r_port}/{r_db}" if r_pass else f"redis://{r_host}:{r_port}/{r_db}"
        _redis_client = aioredis.from_url(url, decode_responses=True)
        await _redis_client.ping()
        logger.info("Attack detector Redis connected: %s", url.split("@")[-1])
        return _redis_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Attack detector Redis unavailable: %s", e)
        _redis_client = None
        return None


# ── Schema ────────────────────────────────────────────────────────────────────
ATTACK_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {ATTACK_TABLE} (
    attack_type LowCardinality(String),  -- sandwich, pump_dump, flash_loan
    confidence LowCardinality(String),   -- HIGH, MEDIUM, LOW
    chain LowCardinality(String),
    block_number UInt64,
    tx_hashes Array(String),
    token_address String,
    pair_address String,
    value_usd Nullable(Float64),
    detected_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (chain, block_number, attack_type)
"""

_ATTACK_INSERT_COLS = (
    "attack_type",
    "confidence",
    "chain",
    "block_number",
    "tx_hashes",
    "token_address",
    "pair_address",
    "value_usd",
    "detected_at",
)


def _ensure_attack_table() -> bool:
    """Create wallet_memory.attack_events if it does not exist yet."""
    client = _get_ch_client()
    if client is None:
        return False
    try:
        client.execute(ATTACK_TABLE_DDL, settings={"max_execution_time": MAX_EXECUTION_TIME})
        return True
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("attack_events table ensure failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 1. SANDWICH DETECTOR
# ═══════════════════════════════════════════════════════════════════════════


async def detect_sandwiches(chain: str, hours_back: int = 1, force_fresh: bool = False) -> list[dict[str, Any]]:
    """Detect sandwich attacks from dex_events (same block, same pair).

    Pattern: attacker buy (amount0_in > 0) → victim swap → attacker sell
    (amount0_out > 0), all in the same block, same contract_address, with
    sender(A) == sender(C).

    Args:
        chain: Chain name (ethereum, bsc, ...).
        hours_back: Lookback window in hours (default 1 — periodic job window).
        force_fresh: Bypass the in-process cache.

    Returns:
        List of sandwich event dicts (max 100).
    """
    chain = _normalize_chain(chain)
    cache_key = f"sandwich:{chain}:{hours_back}"
    cached = _get_cached(cache_key)
    if not force_fresh and cached is not None:
        return cached

    events: list[dict[str, Any]] = []
    client = _get_ch_client()
    if client is None:
        return events

    def _query() -> list[tuple]:
        params: dict[str, Any] = {
            "chain": chain,
            "hours": max(hours_back, 1),
            "limit": MAX_EVENTS_PER_SCAN,
        }
        try:
            return client.execute(
                f"""
                SELECT
                    a.sender AS attacker, a.block_number, a.tx_hash AS buy_tx,
                    b.tx_hash AS victim_tx, c.tx_hash AS sell_tx,
                    a.amount0_in AS buy_amount, c.amount0_out AS sell_amount,
                    a.contract_address AS pair_address,
                    a.token0 AS token0, a.token1 AS token1,
                    a.block_timestamp AS block_timestamp,
                    c.log_index AS sell_log_index
                FROM {DEX_EVENTS_TABLE} a
                JOIN {DEX_EVENTS_TABLE} b
                    ON a.block_number = b.block_number
                   AND a.contract_address = b.contract_address
                   AND a.chain = b.chain
                JOIN {DEX_EVENTS_TABLE} c
                    ON a.block_number = c.block_number
                   AND a.contract_address = c.contract_address
                   AND a.chain = c.chain
                   AND a.sender = c.sender
                WHERE a.chain = %(chain)s
                  AND a.type = 'swap' AND b.type = 'swap' AND c.type = 'swap'
                  AND a.block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND b.block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND c.block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND a.amount0_in > 0
                  AND c.amount0_out > 0
                  AND a.tx_hash < b.tx_hash
                  AND b.tx_hash < c.tx_hash
                  AND a.tx_hash != c.tx_hash
                ORDER BY a.block_number DESC
                LIMIT %(limit)s
                """,  # noqa: S608
                params,
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("sandwich query failed for %s: %s", chain, exc)
            return []

    rows = await run_sync(_query)
    for row in rows:
        try:
            attacker, block_number, buy_tx, victim_tx, sell_tx = row[0], row[1], row[2], row[3], row[4]
            buy_amount = _to_float(row[5])
            sell_amount = _to_float(row[6])
            pair_address = row[7]
            token0 = row[8]
            _token1 = row[9]
            block_ts = row[10]

            if buy_amount <= 0 or sell_amount <= 0:
                continue

            token_address = token0 or pair_address
            balance_ratio = abs(buy_amount - sell_amount) / max(buy_amount, sell_amount)
            if balance_ratio <= SANDWICH_BALANCED_RATIO and sell_amount >= buy_amount:
                confidence = "HIGH"
            elif balance_ratio <= SANDWICH_BALANCED_RATIO:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            events.append(
                {
                    "attack_type": "sandwich",
                    "confidence": confidence,
                    "chain": chain,
                    "block_number": int(block_number),
                    "tx_hashes": [str(buy_tx), str(victim_tx), str(sell_tx)],
                    "token_address": str(token_address).lower(),
                    "pair_address": str(pair_address).lower(),
                    "value_usd": None,  # enriched below
                    "raw_amount": buy_amount,  # token0 units of the attack leg
                    "attacker": str(attacker).lower(),
                    "evidence": [
                        f"same block {block_number}",
                        f"buy {buy_amount:.6g} / sell {sell_amount:.6g} token0",
                        f"victim tx {victim_tx}",
                    ],
                    "block_timestamp": str(block_ts),
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("sandwich row parse failed: %s", exc)
            continue

    # Enrich with USD estimates (capped — price oracle per token)
    events = await _enrich_usd(events[:MAX_EVENTS_PER_SCAN])

    _set_cached(cache_key, events)
    logger.info("attack_detector_sandwich", chain=chain, count=len(events), hours=hours_back)
    return events


# ═══════════════════════════════════════════════════════════════════════════
# 2. PUMP & DUMP DETECTOR
# ═══════════════════════════════════════════════════════════════════════════


async def detect_pump_dumps(chain: str, lookback_hours: int = 24, force_fresh: bool = False) -> list[dict[str, Any]]:
    """Detect pump-and-dump campaigns from dex_events per pair.

    Pattern over the lookback window (24h):
      - Volume spike:  4h volume > 10x trailing-24h baseline
      - Price spike:   price increase > 100% from baseline
      - Drawdown:      price drops > 50% from peak within 24h

    Args:
        chain: Chain name.
        lookback_hours: Analysis window (default 24).
        force_fresh: Bypass the in-process cache.

    Returns:
        List of pump_dump event dicts.
    """
    chain = _normalize_chain(chain)
    cache_key = f"pump_dump:{chain}:{lookback_hours}"
    cached = _get_cached(cache_key)
    if not force_fresh and cached is not None:
        return cached

    events: list[dict[str, Any]] = []
    client = _get_ch_client()
    if client is None:
        return events

    # ── Step 1: candidate pairs with meaningful swap activity ──
    def _query_pairs() -> list[tuple]:
        params: dict[str, Any] = {
            "chain": chain,
            "lookback": max(lookback_hours, 1),
            "min_swaps": PND_MIN_PAIR_SWAPS,
            "limit": 50,
        }
        try:
            return client.execute(
                f"""
                SELECT contract_address, token0, token1, count() AS swaps,
                       sum(toFloat64(amount0_in)) + sum(toFloat64(amount0_out)) AS vol0
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
                GROUP BY contract_address, token0, token1
                HAVING count() >= %(min_swaps)s
                ORDER BY vol0 DESC
                LIMIT %(limit)s
                """,  # noqa: S608
                params,
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("pump_dump pairs query failed for %s: %s", chain, exc)
            return []

    pairs = await run_sync(_query_pairs)

    for pair_row in pairs[:50]:
        try:
            pair_address, token0, token1 = pair_row[0], pair_row[1], pair_row[2]
            pair_address = str(pair_address).lower()
            token0 = str(token0 or "").lower()
            token1 = str(token1 or "").lower()
            token_address = token0 or pair_address

            # ── Step 2: volume windows (4h vs trailing 24h) ──
            vol_4h, vol_24h = await _query_pair_volume(client, chain, pair_address)
            if vol_24h <= 0 or vol_4h <= 0:
                continue

            baseline_4h = max(vol_24h - vol_4h, 0.0) / 5.0  # other 20h normalized to a 4h window
            spike_ratio = vol_4h / baseline_4h if baseline_4h > 0 else float("inf")
            volume_spike = spike_ratio > PND_VOLUME_SPIKE_MULT

            # ── Step 3: price series (reserve ratios per block) ──
            prices, first_ts, last_ts, peak_block = await _query_price_series(
                client, chain, pair_address, lookback_hours
            )
            if len(prices) < 2:
                continue

            baseline_price = _median(prices[: max(1, len(prices) // 5)])
            peak_price = max(prices)
            last_price = prices[-1]
            price_spike_pct = ((peak_price - baseline_price) / baseline_price * 100.0) if baseline_price > 0 else 0.0
            drawdown_pct = ((peak_price - last_price) / peak_price * 100.0) if peak_price > 0 else 0.0
            price_spike = price_spike_pct > PND_PRICE_SPIKE_PCT
            drawdown = drawdown_pct > PND_DRAWDOWN_PCT

            if not volume_spike:
                continue

            if volume_spike and price_spike and drawdown:
                confidence = "HIGH"
            elif volume_spike and price_spike:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            events.append(
                {
                    "attack_type": "pump_dump",
                    "confidence": confidence,
                    "chain": chain,
                    "block_number": int(peak_block or 0),
                    "tx_hashes": [],
                    "token_address": token_address,
                    "pair_address": pair_address,
                    "value_usd": None,
                    "attacker": "",
                    "evidence": [
                        f"vol spike {spike_ratio:.1f}x (4h {vol_4h:.4g} vs 24h {vol_24h:.4g})",
                        f"price spike {price_spike_pct:.0f}%",
                        f"drawdown {drawdown_pct:.0f}% from peak",
                        f"window {first_ts} → {last_ts}",
                    ],
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("pump_dump pair parse failed: %s", exc)
            continue

    events = await _enrich_usd(events[:MAX_EVENTS_PER_SCAN])
    _set_cached(cache_key, events)
    logger.info("attack_detector_pump_dump", chain=chain, count=len(events), lookback_hours=lookback_hours)
    return events


async def _query_pair_volume(client: Any, chain: str, pair_address: str) -> tuple[float, float]:
    """Return (vol_4h, vol_24h) for a pair using both token sides."""

    def _query() -> list[tuple]:
        params: dict[str, Any] = {"chain": chain, "pair": pair_address}
        try:
            return client.execute(
                f"""
                SELECT
                    sumIf(
                        toFloat64(amount0_in) + toFloat64(amount0_out)
                        + toFloat64(amount1_in) + toFloat64(amount1_out),
                        block_timestamp >= now() - INTERVAL 4 HOUR
                    ) AS vol_4h,
                    sum(
                        toFloat64(amount0_in) + toFloat64(amount0_out)
                        + toFloat64(amount1_in) + toFloat64(amount1_out)
                    ) AS vol_24h
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND contract_address = %(pair)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL 24 HOUR
                """,  # noqa: S608
                params,
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("pair volume query failed: %s", exc)
            return []

    rows = await run_sync(_query)
    if not rows or not rows[0]:
        return 0.0, 0.0
    try:
        return _to_float(rows[0][0]), _to_float(rows[0][1])
    except (ValueError, TypeError, IndexError):
        return 0.0, 0.0


async def _query_price_series(
    client: Any, chain: str, pair_address: str, lookback_hours: int
) -> tuple[list[float], str, str, int]:
    """Return (prices, first_ts, last_ts, peak_block) from reserve-ratio swaps.

    Price proxy: token1-per-token0 rate from swaps where amount0_in > 0
    (following the C6 spec's argMax price series).
    """

    def _query() -> list[tuple]:
        params: dict[str, Any] = {"chain": chain, "pair": pair_address, "lookback": max(lookback_hours, 1)}
        try:
            return client.execute(
                f"""
                SELECT block_number, block_timestamp,
                       argMax(
                           toFloat64(amount1_out) / NULLIF(toFloat64(amount0_in), 0),
                           block_number
                       ) AS price
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND contract_address = %(pair)s
                  AND type = 'swap'
                  AND amount0_in > 0
                  AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
                GROUP BY block_number, block_timestamp
                ORDER BY block_timestamp
                """,  # noqa: S608
                params,
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("price series query failed: %s", exc)
            return []

    rows = await run_sync(_query)
    prices: list[float] = []
    first_ts = ""
    last_ts = ""
    peak_block = 0
    peak_price = 0.0
    for row in rows:
        try:
            block_number = int(row[0])
            ts = str(row[1])
            price = _to_float(row[2])
            if price <= 0:
                continue
            if not first_ts:
                first_ts = ts
            last_ts = ts
            prices.append(price)
            if price > peak_price:
                peak_price = price
                peak_block = block_number
        except (ValueError, TypeError, IndexError):
            continue
    return prices, first_ts, last_ts, peak_block


# ═══════════════════════════════════════════════════════════════════════════
# 3. FLASH LOAN DETECTOR (v1 pattern-based)
# ═══════════════════════════════════════════════════════════════════════════


async def detect_flash_loans(chain: str, hours_back: int = 1, force_fresh: bool = False) -> list[dict[str, Any]]:
    """Detect flash-loan patterns from dex_events (same-tx multi-swap).

    v1 pattern: a tx_hash with >= 4 swap events where a single pair shows
    total in ≈ total out (repay) with the target swap in between.

    Args:
        chain: Chain name.
        hours_back: Lookback window in hours (default 1).
        force_fresh: Bypass the in-process cache.

    Returns:
        List of flash_loan event dicts.
    """
    chain = _normalize_chain(chain)
    cache_key = f"flash_loan:{chain}:{hours_back}"
    cached = _get_cached(cache_key)
    if not force_fresh and cached is not None:
        return cached

    events: list[dict[str, Any]] = []
    client = _get_ch_client()
    if client is None:
        return events

    def _query() -> list[tuple]:
        params: dict[str, Any] = {"chain": chain, "hours": max(hours_back, 1), "limit": MAX_EVENTS_PER_SCAN}
        try:
            return client.execute(
                f"""
                SELECT tx_hash, contract_address, token0, token1,
                       count() AS swap_count,
                       sum(toFloat64(amount0_in)) AS total_in,
                       sum(toFloat64(amount0_out)) AS total_out,
                       argMax(block_number, block_number) AS block_number,
                       argMax(block_timestamp, block_timestamp) AS block_timestamp
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND tx_hash IN (
                      SELECT tx_hash
                      FROM {DEX_EVENTS_TABLE}
                      WHERE chain = %(chain)s
                        AND type = 'swap'
                        AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                      GROUP BY tx_hash
                      HAVING count() >= 4
                  )
                GROUP BY tx_hash, contract_address, token0, token1
                HAVING count() >= 2
                ORDER BY block_timestamp DESC
                LIMIT %(limit)s
                """,  # noqa: S608
                params,
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("flash loan query failed for %s: %s", chain, exc)
            return []

    rows = await run_sync(_query)
    for row in rows:
        try:
            tx_hash, pair_address, token0, _token1 = row[0], row[1], row[2], row[3]
            swap_count = int(row[4])
            total_in = _to_float(row[5])
            total_out = _to_float(row[6])
            block_number = int(row[7])
            block_ts = row[8]

            denom = max(total_in, total_out)
            if denom <= 0:
                continue
            repay_diff = abs(total_in - total_out) / denom
            if repay_diff > FLASH_REPAY_TOLERANCE:
                continue

            if swap_count >= 4:
                confidence = "HIGH"
            elif swap_count >= 3:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            token_address = str(token0 or "").lower() or str(pair_address).lower()
            events.append(
                {
                    "attack_type": "flash_loan",
                    "confidence": confidence,
                    "chain": chain,
                    "block_number": block_number,
                    "tx_hashes": [str(tx_hash)],
                    "token_address": token_address,
                    "pair_address": str(pair_address).lower(),
                    "value_usd": None,
                    "raw_amount": total_out,  # token0 units flash-loaned
                    "attacker": "",
                    "evidence": [
                        f"{swap_count} swaps in one tx",
                        f"repay diff {repay_diff * 100:.1f}% (in {total_in:.6g} / out {total_out:.6g})",
                        f"block {block_ts}",
                    ],
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("flash loan row parse failed: %s", exc)
            continue

    events = await _enrich_usd(events[:MAX_EVENTS_PER_SCAN])
    _set_cached(cache_key, events)
    logger.info("attack_detector_flash_loan", chain=chain, count=len(events), hours=hours_back)
    return events


# ═══════════════════════════════════════════════════════════════════════════
# USD enrichment (best-effort, uses our own price oracle)
# ═══════════════════════════════════════════════════════════════════════════


async def _enrich_usd(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Estimate value_usd per event using the own-dex-data price oracle.

    Uses each event's raw token0 amount (``raw_amount``) x token TWAP price.
    Cached per token; failures degrade to None (never raises).
    """
    for ev in events:
        token = ev.get("token_address", "")
        raw_amount = _to_float(ev.get("raw_amount"))
        if not token or raw_amount <= 0:
            continue
        usd = await _estimate_usd(token, ev["chain"], raw_amount)
        if usd is not None:
            ev["value_usd"] = usd
    return events


async def _estimate_usd(token_address: str, chain: str, raw_amount: float) -> float | None:
    """Convert a raw token amount to USD using our own price oracle."""
    if not token_address or raw_amount <= 0:
        return None
    try:
        price_usd = _USD_PRICE_CACHE.get(token_address)
        if price_usd is None:
            price = await get_token_price_simple(token_address, chain, lookback_minutes=60)
            price_usd = float(price.get("price_usd") or 0)
            if price_usd <= 0:
                return None
            _USD_PRICE_CACHE[token_address] = price_usd
            if len(_USD_PRICE_CACHE) > _USD_PRICE_CACHE_MAX:
                _USD_PRICE_CACHE.pop(next(iter(_USD_PRICE_CACHE)), None)
        amt = _normalize_amount(raw_amount, token_address, chain)
        if amt <= 0:
            return None
        return round(amt * price_usd, 2)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("attack_usd_estimate_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Orchestrator — run all detectors, dedupe, persist, wire alerts
# ═══════════════════════════════════════════════════════════════════════════


async def run_attack_detection(
    chain: str = "all",
    hours_back: int = 1,
    notify: bool = True,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Run all three attack detectors and wire results into the alert pipeline.

    Steps:
      1. Check the Redis window cache (TTL 15m) — skip already-scanned blocks.
      2. Run sandwich / pump_dump / flash_loan detectors against dex_events.
      3. Dedupe against per-event Redis keys (overlapping windows).
      4. Persist NEW events to wallet_memory.attack_events.
      5. Push Redis attack_alert:{chain}:{token} for real-time consumers.
      6. Send Telegram scanner alert when confidence == HIGH.
      7. Mark attacker wallets as whale in dex_events.

    Args:
        chain: Chain name or "all" (default).
        hours_back: Scan window in hours (default 1).
        notify: Push Redis/Telegram/whale alerts (default True).
        force_fresh: Bypass caches.

    Returns:
        Summary dict with counts per stage.
    """
    summary: dict[str, Any] = {
        "check": "attack_detection",
        "result": "unavailable",
        "source": "own",
        "chains_scanned": [],
        "events": [],
        "events_new": 0,
        "inserted": 0,
        "alerts_pushed": 0,
        "telegram_sent": 0,
        "whale_marked": 0,
        "cached": False,
        "duration_ms": 0.0,
    }
    _start = time.monotonic()

    chains = [chain] if chain and chain != "all" else list(ALL_CHAINS)
    chains = [_normalize_chain(c) for c in chains]

    try:
        # 1. Redis window cache — skip re-scanning the same blocks
        if not force_fresh:
            cache_hit = await _check_window_cache(chains, hours_back)
            if cache_hit:
                summary["result"] = "scanned"
                summary["cached"] = True
                summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
                return summary

        all_events: list[dict[str, Any]] = []
        for chain_name in chains:
            try:
                sandwich = await detect_sandwiches(chain_name, hours_back, force_fresh)
                pump_dump = await detect_pump_dumps(chain_name, 24, force_fresh)
                flash_loan = await detect_flash_loans(chain_name, hours_back, force_fresh)
                all_events.extend(sandwich)
                all_events.extend(pump_dump)
                all_events.extend(flash_loan)
                summary["chains_scanned"].append(chain_name)
            except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
                logger.warning("attack detection failed for %s: %s", chain_name, exc)
                continue

        if not all_events:
            summary["result"] = "scanned"
            summary["events"] = []
            summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
            await _store_window_cache(chains, hours_back)
            return summary

        summary["events"] = all_events

        # 2-3. Dedupe against per-event Redis keys (overlapping scan windows)
        new_events = await _dedupe_new_events(all_events)
        summary["events_new"] = len(new_events)
        if not new_events:
            summary["result"] = "scanned"
            summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
            await _store_window_cache(chains, hours_back)
            return summary

        # 4. Persist to wallet_memory.attack_events
        summary["inserted"] = _insert_attack_events(new_events)

        # 5-7. Alert pipeline
        if notify:
            summary["alerts_pushed"] = await _push_redis_alerts(new_events)
            summary["telegram_sent"] = await _send_telegram_alerts(new_events)
            summary["whale_marked"] = await _mark_attacker_whale(new_events)

        await _store_window_cache(chains, hours_back)
        summary["result"] = "scanned"
    except (
        httpx.HTTPError,
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
        RedisError,
    ) as exc:
        logger.warning("run_attack_detection failed: %s", exc)
        summary["result"] = "error"
        summary["error"] = str(exc)[:200]

    summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
    logger.info("attack_detection_run", chains=summary["chains_scanned"], new=summary["events_new"])
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# Persistence + alert wiring
# ═══════════════════════════════════════════════════════════════════════════


def _insert_attack_events(events: list[dict[str, Any]]) -> int:
    """Insert attack events into wallet_memory.attack_events. Returns rows inserted."""
    if not events:
        return 0
    client = _get_ch_client()
    if client is None:
        return 0
    if not _ensure_attack_table():
        return 0

    now_ts = datetime.now(UTC)
    rows = [
        (
            ev.get("attack_type", "unknown"),
            ev.get("confidence", "LOW"),
            ev.get("chain", ""),
            int(ev.get("block_number", 0) or 0),
            ev.get("tx_hashes", []) or [],
            ev.get("token_address", ""),
            ev.get("pair_address", ""),
            ev.get("value_usd"),
            now_ts,
        )
        for ev in events
    ]
    try:
        return client.execute(
            f"INSERT INTO {ATTACK_TABLE} ({', '.join(_ATTACK_INSERT_COLS)}) VALUES",
            rows,
            settings={"max_execution_time": MAX_EXECUTION_TIME},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("attack_events insert failed: %s", exc)
        return 0


async def _push_redis_alerts(events: list[dict[str, Any]]) -> int:
    """Push attack_alert:{chain}:{token} keys for real-time consumers."""
    redis = await _get_redis()
    if redis is None:
        return 0
    pushed = 0
    try:
        for ev in events:
            key = f"attack_alert:{ev.get('chain', '')}:{ev.get('token_address', '')}"
            payload = {
                "attack_type": ev.get("attack_type"),
                "confidence": ev.get("confidence"),
                "chain": ev.get("chain"),
                "block_number": ev.get("block_number"),
                "tx_hashes": ev.get("tx_hashes", []),
                "token_address": ev.get("token_address"),
                "pair_address": ev.get("pair_address"),
                "value_usd": ev.get("value_usd"),
                "detected_at": datetime.now(UTC).isoformat(),
            }
            await redis.rpush(key, json.dumps(payload))
            await redis.expire(key, CACHE_TTL_SECONDS)
            await redis.ltrim(key, -50, -1)  # keep last 50 alerts per key
            pushed += 1
        return pushed
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.warning("attack redis push failed: %s", exc)
        return pushed


async def _send_telegram_alerts(events: list[dict[str, Any]]) -> int:
    """Send Telegram scanner alerts for HIGH-confidence attack events."""
    sent = 0
    for ev in events:
        if ev.get("confidence") != "HIGH":
            continue
        try:
            from app.domains.telegram.alert_hooks import send_scanner_alert

            attack_label = str(ev.get("attack_type", "attack")).upper()
            alert_name = "Attack Alert"
            threats = [f"{ev.get('attack_type')} attack detected", *ev.get("evidence", [])[:4]]
            await send_scanner_alert(
                token_address=ev.get("token_address", ""),
                chain=ev.get("chain", ""),
                risk_score=100.0,
                threats=threats,
                token_name=alert_name,
                token_symbol=attack_label,
            )
            sent += 1
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("attack telegram alert failed: %s", exc)
    return sent


async def _mark_attacker_whale(events: list[dict[str, Any]]) -> int:
    """Mark attacker wallets as whale in dex_events (ClickHouse ALTER UPDATE).

    Only sandwich events carry an attacker (front-run sender == back-run
    sender). Grouped per chain so the mutation touches the right rows.
    """
    attackers_by_chain: dict[str, set[str]] = {}
    for ev in events:
        attacker = str(ev.get("attacker", "") or "").lower()
        if ev.get("attack_type") == "sandwich" and attacker:
            attackers_by_chain.setdefault(str(ev.get("chain", "")), set()).add(attacker)
    if not attackers_by_chain:
        return 0
    client = _get_ch_client()
    if client is None:
        return 0

    def _query(chain_name: str, attackers: list[str]) -> int:
        try:
            return client.execute(
                f"""
                ALTER TABLE {DEX_EVENTS_TABLE}
                UPDATE whale = 1
                WHERE chain = %(chain)s
                  AND sender IN %(attackers)s
                SETTINGS mutations_sync = 1
                """,
                {"chain": chain_name, "attackers": attackers},
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.warning("whale marking failed for %s: %s", chain_name, exc)
            return 0

    marked = 0
    for chain_name, attackers in attackers_by_chain.items():
        try:
            marked += await run_sync(_query, chain_name, list(attackers))
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
            logger.warning("whale marking async failed for %s: %s", chain_name, exc)
    return marked


# ═══════════════════════════════════════════════════════════════════════════
# Caching (in-process + Redis window cache + per-event dedup)
# ═══════════════════════════════════════════════════════════════════════════


def _cache_key(parts: tuple[str, ...]) -> str:
    return ":".join(parts)


def _get_cached(key: str) -> list[dict[str, Any]] | None:
    """Return cached detector results if fresh (< 15m)."""
    entry = _RESULT_CACHE.get(key)
    if entry is None:
        return None
    if time.time() - entry.get("_ts", 0) > CACHE_TTL_SECONDS:
        _RESULT_CACHE.pop(key, None)
        return None
    return entry.get("events")


def _set_cached(key: str, events: list[dict[str, Any]]) -> None:
    """Store detector results with a timestamp."""
    _RESULT_CACHE[key] = {"_ts": time.time(), "events": events}
    if len(_RESULT_CACHE) > _RESULT_CACHE_MAX:
        _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)), None)


async def _check_window_cache(chains: list[str], hours_back: int) -> bool:
    """Return True when every chain's scan window is already cached in Redis."""
    redis = await _get_redis()
    if redis is None:
        return False
    try:
        for chain_name in chains:
            key = _cache_key(("attack_detect_window", chain_name, str(hours_back)))
            if not await redis.exists(key):
                return False
        return True
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("window cache check failed: %s", exc)
        return False


async def _store_window_cache(chains: list[str], hours_back: int) -> None:
    """Cache the scan window in Redis (TTL 15m) so we never re-scan the same blocks."""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        payload = json.dumps({"hours_back": hours_back, "scanned_at": datetime.now(UTC).isoformat()})
        for chain_name in chains:
            key = _cache_key(("attack_detect_window", chain_name, str(hours_back)))
            await redis.set(key, payload, ex=CACHE_TTL_SECONDS)
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("window cache store failed: %s", exc)


async def _dedupe_new_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only events not seen in the last 15m (Redis SET NX per event)."""
    redis = await _get_redis()
    if redis is None:
        return events  # no Redis → allow (in-process cache still guards)

    new_events: list[dict[str, Any]] = []
    try:
        for ev in events:
            dedup_key = _cache_key(
                (
                    "attack_seen",
                    ev.get("chain", ""),
                    ev.get("attack_type", ""),
                    str(ev.get("block_number", 0)),
                    ev.get("pair_address", ""),
                )
            )
            created = await redis.set(dedup_key, "1", nx=True, ex=CACHE_TTL_SECONDS)
            if created:
                new_events.append(ev)
        return new_events
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("event dedup failed: %s", exc)
        return events


def _to_float(value: Any) -> float:
    """Safe float conversion (None → 0.0)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _median(values: list[float]) -> float:
    """Median of a list of floats (0.0 for empty)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


# ═══════════════════════════════════════════════════════════════════════════
# Periodic job + CLI
# ═══════════════════════════════════════════════════════════════════════════


async def run_periodic_detection(hours_back: int = 1, notify: bool = True) -> dict[str, Any]:
    """Run attack detection across all chains — the 5-minute cron entry point."""
    return await run_attack_detection(chain="all", hours_back=hours_back, notify=notify)


def get_cache_stats() -> dict[str, Any]:
    """Return cache/connection stats for monitoring."""
    return _common_get_cache_stats(
        _RESULT_CACHE,
        _RESULT_CACHE_MAX,
        CACHE_TTL_SECONDS,
        extra={
            "usd_price_cache_size": len(_USD_PRICE_CACHE),
            "clickhouse_connected": _ch_client is not None,
            "redis_connected": _redis_client is not None,
            "max_execution_time": MAX_EXECUTION_TIME,
        },
    )


def clear_cache() -> None:
    """Clear in-process caches (for testing)."""
    _common_clear_cache(_RESULT_CACHE, _USD_PRICE_CACHE)


async def main() -> None:
    """CLI entry point — run periodic detection once and print a summary.

    Schedule via cron/systemd every 5 minutes:
        python -m app.domains.scanner.engines.attack_detector
    """
    import argparse

    parser = argparse.ArgumentParser(description="C6 Time-Based Attack Detection (ClickHouse-only)")
    parser.add_argument("--chain", default="all", help="Chain name or 'all'")
    parser.add_argument("--hours-back", type=int, default=1, help="Scan window in hours")
    parser.add_argument("--no-notify", action="store_true", help="Skip Redis/Telegram/whale wiring")
    parser.add_argument("--force-fresh", action="store_true", help="Bypass caches")
    args = parser.parse_args()

    result = await run_attack_detection(
        chain=args.chain,
        hours_back=args.hours_back,
        notify=not args.no_notify,
        force_fresh=args.force_fresh,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
