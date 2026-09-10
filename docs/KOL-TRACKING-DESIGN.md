# KOL Labeling + Wallet Tracking System — Design Spec

**Status:** draft · **Owner:** RMI Engineering · **Date:** 2026-08-13
**Scope:** READ-ONLY design. No code changes.

## 0. Current State (as-read)

| Asset | Location | What exists |
|---|---|---|
| KOL registry | `app/databus/x_intel.py` → `CT_ACCOUNTS` | 6 tiers (`crypto_news`, `analysts`, `defi_traders`, `solana_meme`, `macro_vc`, `extended`), 136 accounts; each `{handle, name, category, weight}`. Categories are free-form strings. **No wallet linkage.** |
| `influential_accounts.json` | — | **Does not exist.** Registry is in-code. |
| KOL call tracker | `app/databus/social_intel.py` | In-memory `KOL_DATABASE` (handle → calls: `buy/sell/shill/warning/analysis`, `price_at_call`); trust score; text-pattern shill detector (`SHILL_PATTERNS`: coordinated_posts, paid_promotion, pump_and_dump, bot_engagement, affiliate_farming). **Nothing persisted, no on-chain join.** |
| Label store (canonical) | `rugmunch-scanner/rugmunch_scanner/labels/` | ClickHouse `rugmunch_scanner.labels` — ReplacingMergeTree on `(chain,address,source,label)`; columns `chain,address,label,category,source,confidence,evidence,snapshot`; 4-tier confidence: `deterministic > strong_heuristic > probabilistic > crowdsourced_unverified`. **This is where KOL attribution evidence must land.** |
| Wallet memory | `app/wallet_memory/` | `EntityResolver` (cross-chain linking: EVM portability, same-deployer, gas-payer, bridge), `LabelService` (ClickHouse ~295k curated + static Redis + live Etherscan/RolodETH/walletLabels.xyz/bluepages + CEX builtins), `engine.py` (deployer intelligence), `clustering.py`, `risk_scoring.py`. |
| Wallet attribution today | — | Address-level only. No X-handle→wallet edges anywhere. |

**Gaps this design closes:** (1) persistent KOL registry, (2) X-handle→wallet attribution with confidence + evidence, (3) sentiment→token→wallet-activity join, (4) on-chain-verified shill detection, (5) feedback loop writing evidence into the canonical label store.

---

## 1. KOL Labeling

Source of truth: migrate `CT_ACCOUNTS` → DB, keep `x_intel.py` as ingest config.

### 1.1 Normalized label model (per account)

| Field | Source | Notes |
|---|---|---|
| `kol_id` | handle.lower() | PK |
| `name`, `x_user_id` | x_intel / xurl user lookup | |
| `tier` | dict key → enum `T1..T6` | T1 must-track … T6 extended |
| `category` | free-form → **controlled vocab** | `breaking, journalism, analysis, trading, onchain, research, memecoins, solana, defi, protocol, vc, macro, regulation, influencer, media, education, etf, oracle, infra, layer2, entertainment` |
| `influence_score` | weight (0.55–1.0) → 0–100 | `round(weight*100)` |
| `kol_type` | derived | `news_org / protocol / vc / individual / tracker_bot` (orgs & bots excluded from shill detection) |
| `followers`, `is_verified` | xurl `/2/users/by/username` | refresh daily |
| `status` | `active / suspended / dormant` | |

### 1.2 Labeling rules
- **Tier** = existing 6-bucket structure (stable, already curated).
- **Category** = normalized via a lookup table in the migration script; unmapped values fall back to `analysis` + a review queue row.
- **kol_type** heuristic: VCs/protocols/news (whitelist by handle suffix/name tokens: `Capital`, `Fndn`, `News`, `Magazine`, `Wire`); everything else `individual`.
- Accounts that are **tracker_bot / news_org** get `shill_detect_enabled=false` — never flag a Reuters account.
- Human review flag on any account with weight < 0.6 (`influencer` bucket) — optional disable.

---

## 2. Wallet Attribution (find KOL wallets)

Each link is a `kol_wallet` row with a **confidence tier + evidence string**, so it plugs into the existing 4-tier merge. Attribution methods, ordered by expected strength:

### 2.1 Self-disclosed (strongest — `deterministic`/`strong_heuristic`)
- X bio / pinned post / website / Linktree scan for `0x…` (EVM) or base58 (Solana) strings → `deterministic` when handle matches post author, `strong_heuristic` if from linked site.
- ENS text records (`com.twitter` → handle), Lens/Farcaster profiles (handle match), RNS (Solana).
- X-account claimed addresses in pinned tweets (archive post URL as evidence).

### 2.2 Public tagging (strong_heuristic → probabilistic)
- Existing label sources already queried by `LabelService`: Etherscan labels, RolodETH, walletLabels.xyz, bluepages.fyi, eth-labels DB (115K+), Arkham entity labels, Lookonchain public reports.
- Match rule: label string contains handle/name (case-insensitive) → `strong_heuristic`; entity-level match (e.g. "Vitalik Buterin" == handle name) → `deterministic` if name is exact and unique.

### 2.3 Airdrop / POAP / ENS evidence (probabilistic)
- ENS name == handle or real name → resolve ENS to address → `strong_heuristic` (ENS is self-asserted).
- Airdrop claims: token airdrop contracts (Uniswap, 1inch, etc.) — address that claimed AND has ENS/eth-labels matching the KOL name → `probabilistic`.
- POAP collections (e.g. ETHGlobal) where the address also holds ENS matching the KOL → `probabilistic`.

### 2.4 On-chain activity (probabilistic — weakest, requires ≥2 signals)
- Reuse `EntityResolver` methods (same-deployer, gas-payer) to cluster candidate wallets, then require corroboration: activity pattern matches (buys tokens the KOL posts about, within hours of posts) → `probabilistic`, auto-escalates to `strong_heuristic` after 3 corroborated signals.
- **Never** auto-promote to `deterministic` without a self-disclosure or authoritative label.

### 2.5 Storage & lifecycle
- Write `kol_wallet` rows to Postgres (operational) **and** mirror the attribution as `source="kol_attribution", category="person"` rows into `rugmunch_scanner.labels` (ClickHouse) with `evidence` = proof URL/tx + method. Confidence tier maps 1:1.
- Manual tagging API (`POST /kol/{id}/wallets`) writes `deterministic` with `evidence="manual_review"`.
- Revocation: wallet disputed → `status=revoked`, re-ingest ClickHouse row with `deterministic` counter-evidence (ReplacingMergeTree handles supersession by `ingested_at`).

---

## 3. Signal Pipeline

```
xurl/cookie/web fetchers (x_intel, per-account timelines)
   → normalize post {author, text, created_at, engagement}
   → entity_resolve author → kol_id  (miss → skip / new-KOL queue)
   → sentiment + token extraction     (Groq free tier w/ rule fallback)
        • sentiment: bullish / bearish / neutral   (LLM, temp 0.2)
        • token: address regex (0x…, base58) + $TICKER symbol map
          + CA match against known token registry; ignore >30-char tickers
   → persist kol_signal (Postgres) + dedupe on (kol_id, post_url)
   → on-chain cross-ref (async worker):
        resolve kol_wallet rows for kol_id
        query wallet_memory / ClickHouse token txs for each wallet
        window = ±24h around posted_at for buys, +1..72h for sells
   → join: signal.token_address == tx.token_address
   → shill detector (below) → shill_alert
   → feedback: verified alerts write evidence rows into rugmunch_scanner.labels
     (source="kol_onchain_evidence", label="@handle:shill_sell")
```

Sentiment model: reuse Groq free tier (`llama-3.1-8b` for post classification, batched) with the `_simple_rundown`-style keyword fallback. Call type derived: bullish = `buy`/`shill` candidate; bearish = `warning`.

---

## 4. Data Model

Postgres (`kol_*` tables), ClickHouse for time-series tx/alert history, label store reuses `rugmunch_scanner.labels`.

```sql
-- 1. Account registry (migrate CT_ACCOUNTS)
CREATE TABLE kol_account (
  kol_id          text PRIMARY KEY,          -- handle lower
  name            text NOT NULL,
  x_user_id       text,
  tier            text NOT NULL,             -- T1..T6
  category        text NOT NULL,             -- controlled vocab
  influence_score numeric(5,2) NOT NULL,
  kol_type        text NOT NULL DEFAULT 'individual',
  shill_detect_enabled boolean NOT NULL DEFAULT true,
  followers       bigint,
  is_verified     boolean DEFAULT false,
  status          text NOT NULL DEFAULT 'active',
  config_json     jsonb,                     -- original x_intel row
  last_synced_at  timestamptz,
  created_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now()
);

-- 2. KOL → wallet links
CREATE TABLE kol_wallet (
  id                bigserial PRIMARY KEY,
  kol_id            text NOT NULL REFERENCES kol_account(kol_id),
  chain             text NOT NULL,           -- ethereum | solana | ...
  address           text NOT NULL,
  attribution_method text NOT NULL,          -- self_disclosed|public_tagging|airdrop_poap_ens|onchain_activity|manual
  confidence        text NOT NULL,           -- 4-tier, matches label store
  evidence          text,                    -- URL / tx hash / ENS name
  status            text NOT NULL DEFAULT 'active', -- active|revoked|unverified
  first_seen_at     timestamptz DEFAULT now(),
  last_verified_at  timestamptz,
  UNIQUE (kol_id, chain, address)
);

-- 3. Post-level signal
CREATE TABLE kol_signal (
  id            bigserial PRIMARY KEY,
  kol_id        text NOT NULL REFERENCES kol_account(kol_id),
  post_url      text UNIQUE,
  post_id       text,
  text_snippet  text,
  sentiment     text NOT NULL,               -- bullish|bearish|neutral
  call_type     text,                        -- buy|sell|shill|warning|analysis
  token_symbol  text,
  token_address text,
  token_chain   text,
  price_at_post numeric,
  engagement    jsonb,                       -- likes/retweets/replies
  posted_at     timestamptz NOT NULL,
  created_at    timestamptz DEFAULT now()
);
CREATE INDEX ix_kol_signal_kol_ts  ON kol_signal (kol_id, posted_at DESC);
CREATE INDEX ix_kol_signal_token   ON kol_signal (token_address, posted_at DESC);

-- 4. On-chain joins (ClickHouse; append-only)
CREATE TABLE kol_wallet_tx (
  wallet       String, chain LowCardinality(String),
  token_address String, token_symbol String,
  tx_type      LowCardinality(String),       -- buy|sell
  amount_usd   Float64, token_amount Float64,
  price        Float64, tx_hash String, ts DateTime64(3)
) ENGINE = MergeTree ORDER BY (wallet, token_address, ts);

-- 5. Shill alerts (Postgres + history in ClickHouse)
CREATE TABLE shill_alert (
  id            bigserial PRIMARY KEY,
  kol_id        text NOT NULL,
  token_symbol  text, token_address text, token_chain text,
  pattern       text NOT NULL,               -- shill_then_dump|buy_then_sell|coordinated
  signal_ids    bigint[],
  wallet_tx_ids bigint[],
  severity      text NOT NULL,               -- critical|high|medium
  buy_after_post boolean DEFAULT false,
  sell_after_post_24h numeric,               -- USD sold in 24h window
  status        text NOT NULL DEFAULT 'open',-- open|confirmed|false_positive
  created_at    timestamptz DEFAULT now()
);
```

### API (FastAPI, under existing `/api/v1`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/kol` | list w/ `?tier=&category=&kol_type=` filters, pagination |
| GET | `/kol/{kol_id}` | profile: labels, wallets, signals, trust score |
| GET | `/kol/{kol_id}/wallets` | attributed wallets + evidence + confidence |
| POST | `/kol/{kol_id}/wallets` | manual attribution (auth: admin) |
| DELETE | `/kol/{kol_id}/wallets/{id}` | revoke |
| GET | `/kol/{kol_id}/signals?token=` | post signals w/ sentiment |
| GET | `/kol/{kol_id}/track-record` | ROI per token: price_at_post vs 7d/30d |
| GET | `/shill-alerts?severity=&status=` | alert feed |
| PATCH | `/shill-alerts/{id}` | confirm / false-positive (feedback loop) |
| POST | `/kol/sync` | refresh X profiles + timelines (cron) |

All read endpoints reuse `wallet_memory` engine for wallet/label enrichment so behavior matches the existing wallet stack.

---

## 5. Shill Detector

**Definition:** KOL posts bullish content ("shill" — hype language, `SHILL_PATTERNS`-adjacent, or LLM-classified bullish with promotional markers) → their attributed wallet **sells** the same token shortly after → flag.

### Algorithm
1. New `kol_signal` with `sentiment=bullish` → candidate (require `shill_detect_enabled` and `kol_type='individual'`).
2. Extract `token_address`/`token_symbol` (CA regex + ticker map).
3. Query `kol_wallet_tx` for all `kol_wallet` rows of `kol_id`, token match.
4. Rules (all windows from `posted_at`):
   - **shill_then_dump (CRITICAL):** any sell 1–72h after post, where wallet bought the token ≤7d before posting → `sell_after_post_24h` recorded.
   - **buy_then_sell (HIGH):** wallet bought token within 24h *after* post (front-run own shill), sells ≤7d later.
   - **coordinated (HIGH):** ≥3 KOLs shill same token in <2h window (existing `detect_shill_campaigns` logic, now with wallet proof).
5. Dedupe per (kol, token, pattern) rolling 7d; severity caps at critical.
6. Feedback: `PATCH /shill-alerts/{id}` → `confirmed` writes `rugmunch_scanner.labels` evidence row (`source="kol_onchain_evidence"`, category `shill_dump`) and decrements KOL trust score; `false_positive` logs reason for pattern tuning. Confirmed alerts surface to `get_shill_alerts`/alert pipeline.
7. Thresholds are config-driven (`SHILL_SELL_WINDOW_H`, `MIN_SELL_USD`) — false-positive tuning without code deploys.

### Guardrails
- News orgs, protocols, VC official accounts never flagged (`kol_type`).
- Sells to a CEX (`LabelService.is_exchange`) count; internal transfers don't.
- < $MIN_SELL_USD (default 100) ignored.
- Wallet confidence < `strong_heuristic` requires 2 corroborating sells before critical.

---

## 6. Open Questions / Risks

- **Privacy/ToS:** per-account timeline fetching at scale (xurl rate limits); stagger fetches, cache.
- **False attribution risk:** onchain_activity matching can link wrong wallets — mitigated by never auto-escalating to deterministic and requiring manual confirmation for public exposure.
- **~250 vs 136 accounts:** `CT_ACCOUNTS` holds 136; the remaining ~110 are org/news feeds from `news_intel` config — confirm scope before sync (orgs likely `shill_detect_enabled=false`).
- **Cost:** Groq free tier for sentiment; fallback rules keep $0.
- Label store writes are idempotent (ReplacingMergeTree) — safe to re-ingest on re-attribution.

## 7. Build Order

1. Migrate `CT_ACCOUNTS` → `kol_account` (tier/category normalization + `kol_type`).
2. `kol_wallet` + attribution crawlers (bio/ENS first — highest yield).
3. `kol_signal` pipeline (sentiment + token extraction).
4. On-chain join worker (wallet_memory tx queries).
5. Shill detector rules + alert API.
6. Feedback loop into `rugmunch_scanner.labels` + trust score.
