# Trending — per-chain ranking and the paid tier

Status: shipped 2026-10-28 (`/api/v1/trending/per-chain`). Owner: backend.

## What existed before

`GET /api/v1/trending` (`app/api/v1/public/trending.py`) → `build_trending`
(`app/trending/service.py`) → `fetch_dex_rows` (`app/trending/dex_rows.py`,
`wallet_memory.dex_events`) → `score_row` (`app/trending/scoring.py`).

Formula (0–100, then multiplicative penalties):

| weight | input | normalisation |
|---|---|---|
| 40 | `sum(volume_usd)` | `log10(1+v)/6`, capped |
| 20 | trades | `trades/150`, capped |
| 15 | unique traders | `traders/80`, capped |
| 15 | price change | `(pct+50)/200` in [-50%,+150%] |
| 10 | buy share | `buys/trades` |

Penalties: quote/stable asset, wrapped native, labelled entity, no price series
(×0.85), stale activity (×0.7). One global list; `chain=` only filters the pool.

## Per-chain formula (new)

One board per chain, own candidate pool, 1h / 6h / 24h windows, exponential
recency decay with half-life = window/2.

| weight | input | source |
|---|---|---|
| 28 | volume USD (decay-weighted rank) | `dex_events.volume_usd` |
| 14 | swap velocity | `dex_events` rows |
| 14 | unique buyers | `uniqExactIf(sender, amt_out>0)` |
| 8 | buy pressure | volume-weighted buy share |
| 8 | momentum | first/last USD price |
| 8 | freshness | first event age / window |
| 10 | holder concentration (inverse) | `holder_balances`, top-10 share |
| 10 | liquidity change — **not available** | no pool reserves indexed |

`coverage` reports how much weight was present; the score normalises by the
available weight, so a missing input shrinks the denominator instead of
punishing the token. Liquidity is reported in `unavailable` with a reason —
never estimated.

Guards (multiplicative, each named in the response): `single_wallet_pump` ×0.5
(top wallet >50% of volume), `churn` ×0.6 (>15 swaps per wallet),
`round_trip` ×0.7 (wallet on both sides >50% of volume), `stale_window` ×0.7,
`risk` ×[0.5–1.0], `scam_overlap` ×0.2.

Wash detection deliberately ignores `sender = recipient`: the indexer writes
the trader into both fields (100% of rows match), so that comparison is
useless — the guards above use wallet concentration and churn instead.

## Paid tier

- **Placement**: a separate `promoted` array. It never occupies an index in
  `boards[].items`; organic scores and ordering are computed before promotions
  are read, so a purchase cannot move a token.
- **Disclosure**: every promoted item carries `placement="promoted"`,
  `disclosure` (verbatim: "Promoted placement — paid. Does not affect organic
  ranking.") and the `advertiser` name.
- **Price**: `PRICE_USD_PER_DAY = 500.00` per chain per day, max
  `MAX_PER_CHAIN = 2` slots per chain, sold by day (`starts_at`/`ends_at`).
  Unsold capacity is not back-filled — an empty slot renders nothing.
- **Refusal**: risk-flagged or scam-overlapping tokens cannot be promoted, even
  when paid; they return in `rejected_promotions` with a reason.
- **Registry**: JSON at `TRENDING_PROMOTIONS_FILE` (default
  `data/trending_promotions.json`). Fail-open: a missing or malformed file
  yields no promotions.

Follow-up (planned, not shipped): move the registry to a Postgres
`trending_promotions` table with billing/audit columns and wire the risk gate to
`rag_scan_labels`/`scam_addresses` rather than a caller-supplied blocklist.

## Data findings that shape the ranking

- `dex_events` is stalled: newest event 2026-10-23 (~5 days before this run).
  The per-chain query anchors on each chain's own newest event and reports
  `stale_s`, so a stale indexer yields a labelled board, not an empty one.
- Price enrichment is uneven: ethereum's last 24h of swaps has `price_usd = 0`
  for every row; arbitrum's last priced swap is 2026-09-27. Those boards rank on
  volume/velocity only until enrichment is fixed.
- `holder_balances` last updated 2026-09-02 — concentration is a snapshot, not
  current. It carries 10 weight and a null when the token is absent.
- `dex_events.amount0/amount1` (reserves) are all zero, so liquidity and
  liquidity-change cannot be derived without an indexer change.
