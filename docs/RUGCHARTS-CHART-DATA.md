# RugCharts Chart Data — free, keyless sources

Every candle served by `/api/v1/rugcharts/ohlcv/*` comes from a **public,
keyless HTTP API**. No paid vendor, no scraping, no browser automation.

Resolution lives in `app/routers/rugcharts/helpers/ohlcv_fallback.py`; each
source is its own small module.

## Sources

| # | Source | Endpoint | Key | Scope | Limits | OHLCV? |
|---|--------|----------|-----|-------|--------|--------|
| 1 | Own indexer | ClickHouse `wallet_memory.dex_ohlcv_hourly` | internal | pool | our DB | hourly MV; re-aggregated for other frames |
| 2 | GeckoTerminal | `/api/v2/networks/{net}/pools/{pool}/ohlcv/{unit}` | none | pool | ~30 req/min public; `limit` ≤ 1000 | yes |
| 3 | Binance klines | `data-api.binance.vision/api/v3/klines` (fallbacks `api.binance.com`, `api.binance.us`) | none | asset | 1200 weight/min; majors only | yes |
| 4 | CoinGecko (free/demo) | `/api/v3/coins/{id}/ohlc` | none (`x-cg-demo-api-key` optional) | asset | ~30 req/min; 429 under load; no volume; coarse buckets | OHLC, volume forced 0 |
| 5 | DexScreener | `/latest/dex/pairs/{chain}/{pool}` | none | pool | generous; pair state only | **no** — one flat snapshot candle |

### Notes that matter

- **Binance is geo-blocked on some hosts.** From Talos, `api.binance.com`
  answers **451**; `data-api.binance.vision` (public market-data mirror) answers
  200 with the same klines. The adapter tries hosts in order and records a
  `geo_blocked` reason when a host refuses.
- **CoinGecko free tier is rate-limited and coarse.** Bucket width is fixed by
  `days` (30-minute for `days=1`), volume is absent, and 429s are common from
  shared cloud IPs. The ladder reports `rate_limited` rather than an empty chart.
- **DexScreener has no OHLCV endpoint.** It publishes live pair state, so the
  adapter returns exactly one candle (`open = high = low = close`, `volume = 0`)
  and the response is marked `degraded: true`. A single point is a price, not a
  trend — the UI must say so.
- **TradingView MCP is not a chart-data API.** It exposes screeners (gainers,
  losers, Bollinger scans, market overviews). It cannot return historical
  candles for an arbitrary pool, so it is deliberately absent from this ladder.
- **Jupiter price API (`lite-api.jup.ag/price/v3`)** is free and keyless but
  price-only (no history), so it is not used as a candle source.

## Fallback order

`clickhouse → geckoterminal → binance → coingecko → dexscreener`

Sources 3 and 4 key on the **token**, not the pool: the ladder resolves the
pool's base token via GeckoTerminal pair metadata, then DexScreener
(`baseToken.address`) when GeckoTerminal is rate-limited. Unknown chains still
get the pool-scoped tail (`clickhouse → geckoterminal → dexscreener`).

## Canonical candle schema

```json
{"time": 1792825200, "open": 102.07, "high": 102.38, "low": 101.75,
 "close": 101.98, "volume": 30269.62}
```

`normalize_candles()` (in `ohlcv_schema.py`) sorts ascending, dedupes by
timestamp (last row wins), drops rows with non-positive prices, `high < low`,
negative volume, or a bad timestamp, and casts `time` to unix seconds / prices
to float. Every adapter returns this shape.

## Provenance instead of a bare null

`fetch_ohlcv()` returns `(candles, meta)`:

```json
{"source": "binance", "scope": "asset", "rows": 48, "chain": "base",
 "timeframe": "1h", "degraded": false, "reason": null,
 "attempts": [{"source": "clickhouse", "reason": "empty_payload", "rows": 0},
              {"source": "geckoterminal", "reason": "rate_limited", "rows": 0},
              {"source": "binance", "reason": null, "rows": 48}]}
```

When nothing serves, `source` is `null` and `reason` names the last failure
(`rate_limited`, `geo_blocked`, `timeout`, `not_found`, `unsupported_asset`,
`empty_payload`, `source_error`, `source_unavailable`). The route exposes this
as `data.ohlcv_meta`.

`degraded` is true for a DexScreener snapshot or when the feed's bucket is wider
than the requested timeframe (`is_coarse`).

Caching: successful results 300 s, empty results 30 s (negative cache), on top
of GeckoTerminal's own 300 s candle cache.

## Verification

- `tests/test_rugcharts_chart_sources.py` — 18 tests: normalization, coarse
  detection, host fallthrough on 451, 429 reasons, zero-volume CoinGecko rows,
  DexScreener snapshot shape, ladder ordering, degraded flag, null+reason, base
  token resolution.
- Live proof (Talos, 2026-10-24): solana + ethereum 48 pool candles from
  GeckoTerminal; base 48 candles from Binance after base-token resolution;
  Binance SOLUSDT 5 rows; CoinGecko `ethereum` 5 rows; GeckoTerminal 429
  reported as `rate_limited` instead of empty.
