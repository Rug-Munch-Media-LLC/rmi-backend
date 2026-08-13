# KOL Call Tracking + Scoring — Methodology Spec

**Status:** draft · **Owner:** RMI Engineering · **Date:** 2026-08-13
**Companion:** `docs/KOL-TRACKING-DESIGN.md` (labeling, wallet attribution, shill detector).
**Inputs:** `app/databus/social_sentiment.py` (sentiment/token extraction, `WINDOWS`, `_label` thresholds), `app/databus/influential_accounts.json` (255-account registry, 9 categories).

## 1. Call Detection

A **call** is a KOL post that takes a directional view on a **specific** token.

Trigger rules (all must hold):
1. Author resolves to `kol_account` and `kol_type='individual'` (orgs/protocols/VC/tracker bots never produce scored calls).
2. Post sentiment is **bullish**: `analyze_sentiment` score ≥ **+0.3** (matches `social_sentiment._label` "bullish"); bearish (≤ −0.3) posts are tracked as `call_type='warning'` but don't enter bull scoring.
3. Post references **exactly one resolvable token**, in priority order: CA regex (`0x…` EVM / base58 Solana) → `$TICKER` cashtag → known-symbol match against the sentiment engine's `KNOWN_TOKENS` (with `BARE_MATCH_STOPWORDS` applied). Multi-token posts are split into one call per token; generic "market" alpha with no token ref is **not** a call.
4. Optional shill boost: hype markers (`gem/moon/alpha/soon/undervalued/buy`) raise detection confidence but never create a call alone.

Dedupe: one call per `(kol_id, token_address, posted_at)`; repeat posts on the same token within **24h** are *reinforcement*, not new calls (prevents spam inflation). Evidence stored: `post_url`, `text_snippet`, engagement.

## 2. Outcome Windows

Capture at `posted_at` (t0): `price, mcap, liquidity_usd` + `price_source`. Evaluate at **t0+1h, t0+24h, t0+7d** (absolute windows from post time, not ingestion). Return `R = p(t)/p0 − 1`.

| Outcome | Rule (config-driven) |
|---|---|
| **PUMP** | R ≥ **+20%** at window AND not rugged |
| **RUG** | mcap drawdown ≥ **−90%** from p0, OR liquidity ≤ 10% of p0 (LP removed), OR honeypot/mint-disabled flag (`token_security.py`/GoPlus), OR price < 10% of p0 with no trades in 24h |
| **DUMP** | R ≤ **−20%** (validates bearish warnings) |
| **FLAT** | −20% < R < +20% |

Primary outcome = **+24h**; **+7d** confirms (a +24h pump that rugs by 7d is re-classified **rug**, final). Thresholds configurable: `PUMP_PCT=0.20`, `RUG_DRAWDOWN=0.90`, `LIQ_RATIO=0.10`, `MIN_SELL_USD` per design §5.

## 3. Call Scoring (per KOL, rolling 90d + lifetime)

With `bull_calls_resolved` = bull calls with a +24h outcome:
- **Win** = +24h R > **+10%** and not rugged; **Loss** = R < −10% or rugged; else flat.
- `win_rate` = wins / resolved
- `avg_return_24h` = mean of bull-call +24h returns, **capped at ±500%** (meme skew); `median_return_24h` reported alongside.
- `rug_rate` = rugged calls / resolved
- `shill_rate` = confirmed `shill_alert`s (design §5) / bull calls.

## 4. Trust Score (0–100)

```
trust = clamp( 50 + 40·((win_rate − 0.5)/0.5) + 20·alpha_norm − 30·rug_rate − 25·shill_rate , 0, 100 ) · coverage
```
- `alpha_norm` = clamp(avg_return_24h / 50, −1, +1)
- `coverage` = min(1, resolved_calls / 20) — Bayesian shrinkage: <20 resolved calls pull score toward 50 (prevents 1-hit wonders).
- Hard floor **0** after any confirmed shill; rug_rate > 0.30 caps score at ≤ 40.
- Accounts with `shill_detect_enabled=false` or `kol_type != individual` return `trust_score: null` (not scored).
- Score tiers: 80–100 *trusted*, 60–79 *cautious*, 40–59 *unproven*, 0–39 *flagged*.

## 5. Data Model (Postgres, extends design §4)

```sql
CREATE TABLE kol_call (
  id            bigserial PRIMARY KEY,
  kol_id        text NOT NULL REFERENCES kol_account(kol_id),
  post_url      text NOT NULL, post_id text,
  text_snippet  text,
  call_type     text NOT NULL,              -- bull|warning
  token_symbol  text, token_address text NOT NULL, token_chain text,
  price_at_call numeric, mcap_at_call numeric, liquidity_at_call numeric,
  price_source  text NOT NULL,              -- dexscreener|gmgn|ohlcv|pyth
  posted_at     timestamptz NOT NULL, created_at timestamptz DEFAULT now(),
  UNIQUE (kol_id, token_address, post_url)
);
CREATE INDEX ix_kol_call_kol_ts  ON kol_call (kol_id, posted_at DESC);
CREATE INDEX ix_kol_call_token   ON kol_call (token_address, posted_at DESC);

CREATE TABLE call_outcome (
  id            bigserial PRIMARY KEY,
  call_id       bigint NOT NULL REFERENCES kol_call(id),
  window        text NOT NULL,              -- 1h|24h|7d
  price         numeric, mcap numeric, return_pct numeric,
  outcome       text NOT NULL,              -- pump|rug|flat|dump
  rugged        boolean DEFAULT false, rug_signal text, -- lp_removed|honeypot|mcap_collapse|no_trades
  resolved_at   timestamptz NOT NULL,
  UNIQUE (call_id, window)
);

CREATE TABLE kol_track_record (
  kol_id          text PRIMARY KEY REFERENCES kol_account(kol_id),
  window_start    date, window_end date,    -- rolling 90d
  resolved_calls  int, bull_calls int, wins int, losses int, flats int, rugged_calls int,
  win_rate        numeric, avg_return_24h numeric, median_return_24h numeric,
  rug_rate        numeric, shill_rate numeric, shill_alerts int,
  trust_score     numeric, updated_at timestamptz DEFAULT now()
);
```

## 6. Price-Source Wiring (primary → fallback)

| Rank | Source | Component | Supplies |
|---|---|---|---|
| 1 | **GMGN** (Solana) | `gmgn_provider.get_token_info` | price, mcap, holder analysis, smart money — memecoins |
| 2 | **DexScreener** (all chains) | `gmgn_provider._fallback_dexscreener_token` / `providers.py` | price, mcap, `liquidity.usd`, txns — rug signal + EVM |
| 3 | **Indexer OHLCV** | `ohlcv_engine.get_candles` / `get_price_change` (chain-indexer trade feed) | historical candles at +1h/+24h/+7d when live snapshot missed |
| 4 | **Pyth** (majors) | `pyth_provider.get_single_price_feed` | sanity check for majors |

Snapshot worker: at t0 and each window, try GMGN (Solana) / DexScreener (EVM) for mcap+liquidity; if unavailable, fall back to OHLCV candle close at the boundary. Rug signals additionally consult `token_security.py` (GoPlus) and `volume_authenticity.py`.

## 7. Build Order & Risks
1. `kol_call` + detection worker (reuse `social_sentiment` token/sentiment output).
2. `call_outcome` + scheduled snapshot worker.
3. `kol_track_record` + trust formula; feedback from `shill_alert.confirmed`.
4. Expose `/kol/{kol_id}/track-record` + `/kol/{kol_id}/trust`.

[REVIEW] (1) "Call" excludes non-bullish content and multi-symbol ambiguity — precision vs recall tradeoff, review threshold +0.3. (2) +20% pump / −90% rug thresholds are arbitrary starting points; tune on 90d backtest of known calls. (3) Capped avg return hides tail risk — median is the robust primary for memecoins. (4) Price source disagreement (GMGN vs DexScreener vs indexer) needs a canonical tiebreak rule; propose indexer close as authority when |Δ| > 5%. (5) 90d rolling window vs lifetime record — both stored; UI should show both. (6) Farcaster ingestion absent (`social_sentiment.py` line 473) — calls will be X/Reddit-only until it lands.
