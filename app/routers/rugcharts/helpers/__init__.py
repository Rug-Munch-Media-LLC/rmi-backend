"""RugCharts helper package — split from the former 1019-line helpers.py.

Sub-modules:
- ``common``     — shared Redis cache, ClickHouse query, constants
- ``ohlcv``      — OHLCV + market data fetching (GeckoTerminal, DexScreener, ClickHouse)
- ``intel``      — scoring, holder-stats ladder, chain freshness
- ``signals``    — trade tape / whale trade signals
- ``formatting`` — formatting/helper utilities
"""

from .common import (
    CACHE_TTL as CACHE_TTL,
    CH_OHLCV_TTL as CH_OHLCV_TTL,
    GECKO_NETWORKS as GECKO_NETWORKS,
    GECKO_TIMEFRAMES as GECKO_TIMEFRAMES,
    HELIUS_KEY as HELIUS_KEY,
    INTERNAL_API_BASE as INTERNAL_API_BASE,
    OHLCV_TTL as OHLCV_TTL,
    RECENT_TRADES_TTL as RECENT_TRADES_TTL,
    REDIS_HOST as REDIS_HOST,
    REDIS_PASSWORD as REDIS_PASSWORD,
    REDIS_PORT as REDIS_PORT,
    WHALE_MIN_USD as WHALE_MIN_USD,
    WHALE_TTL as WHALE_TTL,
    _cache_get_json as _cache_get_json,
    _cache_set_json as _cache_set_json,
    _ch_query as _ch_query,
    _r as _r,
)
from .intel import (
    _chain_freshness as _chain_freshness,
    _compute_rug_score as _compute_rug_score,
    _compute_volume_authenticity as _compute_volume_authenticity,
    _fetch_holder_stats as _fetch_holder_stats,
    _holder_stats_from_clickhouse as _holder_stats_from_clickhouse,
    _holder_stats_from_helius as _holder_stats_from_helius,
    _holder_stats_from_scanner_api as _holder_stats_from_scanner_api,
    _holder_stats_from_solana_transfers as _holder_stats_from_solana_transfers,
)
from .ohlcv import (
    _fetch_dexscreener_trending as _fetch_dexscreener_trending,
    _fetch_ohlcv_clickhouse as _fetch_ohlcv_clickhouse,
    _fetch_ohlcv_gecko as _fetch_ohlcv_gecko,
    _fetch_pair_info as _fetch_pair_info,
    _fetch_single_pair as _fetch_single_pair,
    _timeframe_to_ch_params as _timeframe_to_ch_params,
)
from .signals import (
    _fetch_recent_trades_clickhouse as _fetch_recent_trades_clickhouse,
    _fetch_whale_trades_clickhouse as _fetch_whale_trades_clickhouse,
    _whale_side as _whale_side,
)
