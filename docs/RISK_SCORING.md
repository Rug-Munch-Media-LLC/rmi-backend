# RugMunchScan Risk Scoring

> Status: canonical · owner=Rug Munch Media LLC Engineering · last_updated=2026-08-03

This document explains how RugMunchScan computes token risk scores, what every
risk flag means, and how any score can be traced back to specific findings.
It is the reference for the three scoring implementations in the codebase:

| Engine | Module | Direction | Range |
|---|---|---|---|
| **Unified (canonical)** | `app/domains/scanner/unified/scoring.py` | safety (higher = safer) | 0–100 |
| **Legacy tiered** | `app/domains/scanner/service/scoring.py` | safety (higher = safer) | 0–100 |
| **SENTINEL composite** | `app/domains/scanners/sentinel_pipeline/risk.py` | risk (higher = riskier) | 0–100 |

The **unified engine** is the primary score produced by `UnifiedTokenScanner`
and the one this document centers on. The legacy and SENTINEL engines are
documented for parity, because scan results still flow through them today
(public scanner API → legacy; SENTINEL pipeline → composite risk).

---

## 1. Scoring Methodology

RugMunchScan uses a **multi-dimensional signal aggregation** model to compute a
0–100 safety score. **Lower scores = higher risk.**

### How the unified engine works

1. Every token starts at a **neutral baseline of 50** (unknown/neutral).
2. Each signal contributes a **flat penalty** (risk finding) or **bonus**
   (green finding) attributed to one of five dimensions.
3. The final score is `clamp(0, 100, 50 + Σ deltas)`.
4. Each dimension reports its surviving points out of a weight cap so the
   final number is always traceable (see [Explainability](#3-explainability)).

### Dimensions (weighted)

The five dimension weights are the **maximum points each dimension can
contribute toward safety**. They sum to 100 and define the tiered product
model below.

| Dimension | Weight | Signals | Data Sources |
|---|---|---|---|
| **Security** | 35 | Honeypot, contract verification, mint authority, proxy, owner privileges, sell tax, rug-pattern matches | GoPlus / `token_security` (own ClickHouse, GoPlus, Honeypot.is), `rug_patterns` |
| **Liquidity** | 25 | LP lock %, total liquidity | `liquidity_risk` (own `dex_events`, DexScreener, UNCX/TeamFinance) |
| **Holders** | 15 | Top-10 concentration, Gini/HHI inequality, holder count, exchange funding, wallet freshness, mixer funding, holder overlap | `holder_data` + `holder_health` + `funding_analysis` + `wallet_freshness` + `holder_overlap` (own `evm_transfers`, clever-wallet analysis) |
| **Deployer** | 15 | Reputation score, risk label, rug count, deployment history | `dev_reputation` (own `deployer_events`, chain-indexer) |
| **Market** | 10 | Volume authenticity, premium detections (bundle/MEV/wash/sniper), chain-indexer patterns | `volume_authenticity`, `premium`, `chain_indexer_patterns` (own `dex_events`, DexScreener) |

> **How to read a dimension score:** a dimension's `score` field is
> `clamp(0, max, max + delta)`. `delta` is the dimension's net contribution
> to the final score. The final score is `50 + Σ deltas`, **not** the sum of
> dimension scores.

### Penalty / bonus amounts (unified engine)

| Signal | Condition | Delta | Flag |
|---|---|---|---|
| Security check fail | `check.status == "fail"` | −5 per check | check id (e.g. `HP01`) |
| Security check pass | `check.status == "pass"` | +2 per check | green (check id) |
| Holder concentration | `top_10_pct > 80` | −15 | `HOLDER_CONCENTRATION_HIGH` |
| Holder distribution | `top_10_pct < 30` | +5 | `HOLDER_DISTRIBUTED` |
| Holder inequality | `gini > 0.8` | −10 | `HOLDER_GINI_HIGH` |
| Exchange-funded (extreme) | `funding_analysis.exchange_funded_pct > 50` | −10 | `HIGH_EXCHANGE_FUNDED` |
| Exchange-funded (elevated) | `funding_analysis.exchange_funded_pct > 30` | −5 | `EXCHANGE_FUNDED_ELEVATED` |
| Fresh wallets (extreme) | `funding_analysis.fresh_wallet_pct > 40` or `wallet_freshness.fresh_wallet_pct > 50` | −8 / −10 | `HIGH_FRESH_WALLET_PCT` |
| Fresh wallets (elevated) | `funding_analysis.fresh_wallet_pct > 20` or `wallet_freshness.fresh_wallet_pct > 30` | −4 / −5 | `FRESH_WALLET_ELEVATED` |
| Mixer-funded holders | `funding_analysis.mixer_funded_pct > 5` | −6 | `MIXER_FUNDED_HOLDERS` |
| Holder overlap (scam token) | `holder_overlap.worst_token_is_scam` and `max_overlap_pct > 30` | −10 | `HOLDER_OVERLAP_SCAM` |
| Holder overlap (high) | `holder_overlap.max_overlap_pct > 30` | −5 | `HOLDER_OVERLAP_HIGH` |
| LP lock low | `locked_pct < 50` | −20 | `LP_LOCK_LOW` |
| LP locked | `locked_pct > 90` | +5 | `LP_LOCKED` |
| Deployer high risk | `dev.risk_score > 70` | −25 | `DEV_HIGH_RISK` |
| Deployer reputable | `dev.risk_score < 20` | +5 | `DEV_REPUTABLE` |
| Rug pattern match | `match_count = N` | −10 × N | `RUG_PATTERN_MATCH:N` |
| Fake volume | `fake_volume_pct > 50` | −20 | `VOLUME_FAKE` |
| Authentic volume | `fake_volume_pct < 10` | +5 | `VOLUME_AUTHENTIC` |
| Chain-indexer findings | Σ `risk_score` | −min(Σ, 40) | `ONCHAIN_<PATTERN>` (informational) |
| Premium detection | feature risk `HIGH`/`CRITICAL` | −10 each | `PREMIUM_<FEATURE>` |

> **Clever-wallet holder flags — what triggers them:** these seven flags come
> from the funding-source / wallet-freshness / holder-overlap analysis
> (`funding_analysis`, `wallet_freshness`, `holder_overlap`):
> - `HIGH_EXCHANGE_FUNDED` — >50% of analyzed top holders were funded by CEX
>   (exchange) wallets; `EXCHANGE_FUNDED_ELEVATED` — >30%.
> - `HIGH_FRESH_WALLET_PCT` — >40% fresh wallets from `funding_analysis`, or
>   >50% from `wallet_freshness`; `FRESH_WALLET_ELEVATED` — >20% / >30%
>   respectively. The heavier penalty applies when the source with the higher
>   threshold fires.
> - `MIXER_FUNDED_HOLDERS` — >5% of analyzed holders were funded via a mixer /
>   tumbler.
> - `HOLDER_OVERLAP_SCAM` — `max_overlap_pct > 30` **and** the worst-overlap
>   token is a known scam; `HOLDER_OVERLAP_HIGH` — `max_overlap_pct > 30`
>   (without the scam-token condition).

### Risk levels

The final safety score maps to a risk level (also mirrored by the legacy
`_risk_level_from_safety` helper):

| Safety score | Risk level |
|---|---|
| ≥ 80 | `safe` |
| ≥ 60 | `low` |
| ≥ 40 | `medium` |
| ≥ 20 | `high` |
| < 20 | `critical` |

### Tiered Scoring

The tiered product model maps the five dimensions onto subscription tiers.
The unified scanner gates the **PREMIUM** phase on `elite`/`admin`; CORE,
DEEP and INTEL phases run as free enrichment today.

| Tier | Dimensions included | Weight covered | Unified scanner phase |
|---|---|---|---|
| **CORE (free)** | Security + Liquidity | 60% | CORE + DEEP + INTEL (free enrichment) |
| **DEEP (pro)** | + Holders + Deployer | 90% | CORE + DEEP (+ INTEL free enrichment) |
| **PREMIUM (elite)** | + Market | 100% | CORE + DEEP + PREMIUM + INTEL |

`UnifiedTokenScanner` phase constants (in
`app/domains/scanner/unified/scanner.py`):
`CORE_CHAINS` (price/meta/security), `DEEP_CHAINS` (holders, liquidity, dev,
rug, volume, chain-indexer), `PREMIUM_CHAINS` (bundle, cluster, sniper, bot
farm, copy-trade, insider, wash, MEV, fresh wallet — tier-gated),
`INTEL_CHAINS` (smart money, whales, launches, cross-chain entity).

### Sibling engines (parity reference)

**Legacy tiered** (`app/domains/scanner/service/scoring.py`):
`_calculate_safety_score(modules_results)` starts at 50 and applies flat
penalties per module result: honeypot +35, spam +30, sanctioned +50,
bundled + confidence×50, reputation `CRITICAL` +30 / `HIGH` +20, copycat
`high_risk` +25, LP unlocked +20, proxy owner +25, concentration +15,
top-10 LP ≥ 90% +30, suspicious social +15/reason, age/volume risk levels,
etc. Clean modules add bonuses; ≥ 6 modules with zero risk adds +10 (cap 95).
Flags are generated separately in `service/scan.py` (see
[Legacy flags](#legacy-flags)). **Note:** legacy penalties are not attributed
to dimensions — the unified engine is the explainable implementation.

**SENTINEL composite** (`app/domains/scanners/sentinel_pipeline/risk.py`):
`_compute_composite(scores)` is a weighted average of per-module 0–100 risk
scores: holder 0.20, bundle 0.20, liquidity 0.15, dev 0.15, exchange funding
0.10, bytecode similarity 0.08, wash 0.08, block-zero 0.08, guilt 0.06, gas
0.06, cross-chain 0.05, metadata 0.04, sentiment 0.04, pumpfun 0.04, sleep
0.04, social velocity 0.03; unknown modules default to 0.05. The composite is
a **risk** score (higher = riskier) with bands: `< 25` low, `< 60` moderate,
`< 75` high, else critical — aligned with the Telegram alert gate
(`risk_score >= 60`).

---

## 2. Flag System

Every flag is a machine-readable string attached to a scan result
(`risk_flags` for red, `green_flags` for green). The unified engine emits
the flags below; consumers (API, RugBrain, Telegram alerts) treat them as opaque
labels.

### 2.1 Unified composite flags

| Flag | Dimension | Detected by | Score impact | Plain-English meaning |
|---|---|---|---|---|
| `HOLDER_CONCENTRATION_HIGH` | holders | `holder_data.top_10_pct > 80` | −15 | The top 10 wallets hold more than 80% of supply — a few whales can dump on you. |
| `HOLDER_GINI_HIGH` | holders | `holder_health.gini > 0.8` | −10 | Holder balances are extremely uneven — the distribution is top-heavy. |
| `HIGH_EXCHANGE_FUNDED` | holders | `funding_analysis.exchange_funded_pct > 50` | −10 | More than half of analyzed top holders were funded by CEX wallets — coordinated exchange buy-in. |
| `EXCHANGE_FUNDED_ELEVATED` | holders | `funding_analysis.exchange_funded_pct > 30` | −5 | Elevated share of top holders funded by exchange wallets. |
| `HIGH_FRESH_WALLET_PCT` | holders | `funding_analysis.fresh_wallet_pct > 40` or `wallet_freshness.fresh_wallet_pct > 50` | −8 / −10 | Extreme share of brand-new wallets among top holders — classic airdrop-farming / short-lived setup. |
| `FRESH_WALLET_ELEVATED` | holders | `funding_analysis.fresh_wallet_pct > 20` or `wallet_freshness.fresh_wallet_pct > 30` | −4 / −5 | Elevated share of fresh wallets among top holders. |
| `MIXER_FUNDED_HOLDERS` | holders | `funding_analysis.mixer_funded_pct > 5` | −6 | Some top holders were funded via a mixer/tumbler — obfuscated source of funds. |
| `HOLDER_OVERLAP_SCAM` | holders | `holder_overlap.worst_token_is_scam` and `max_overlap_pct > 30` | −10 | Top holders overlap heavily with a known scam token — likely the same cluster of wallets. |
| `HOLDER_OVERLAP_HIGH` | holders | `holder_overlap.max_overlap_pct > 30` | −5 | Top holders overlap heavily with another token — wallets may be coordinated. |
| `LP_LOCK_LOW` | liquidity | `liquidity_risk.locked_pct < 50` | −20 | The liquidity pool is not locked; the deployer can pull funds at any time. |
| `DEV_HIGH_RISK` | deployer | `dev_reputation.risk_score > 70` | −25 | The deployer has a high-risk history (rugs, honeypots, short-lived tokens). |
| `RUG_PATTERN_MATCH:N` | security | `rug_patterns.match_count = N` | −10 × N | The token matches N known rug-pull patterns (known pattern, liquidity removal, price crash…). |
| `VOLUME_FAKE` | market | `volume_authenticity.fake_volume_pct > 50` | −20 | More than half of the trading volume looks fake/wash-traded. |
| `ONCHAIN_<PATTERN>` | market | chain-indexer finding `detected` | 0 (informational) | On-chain behavior pattern detected (bundler, sniper, pump-dump, mixer interaction…). |
| `PREMIUM_<FEATURE>` | market | premium feature risk `HIGH`/`CRITICAL` | −10 | Elite-tier detection fired (bundle, cluster, sniper, bot farm, copy-trade, insider, wash, MEV). |
| `SCAN_ERROR: <msg>` | — | unhandled scan exception | 0 | The scan hit an error; treat the score as incomplete. |

### 2.2 Green flags (safety bonuses)

| Flag | Dimension | Detected by | Score impact | Meaning |
|---|---|---|---|---|
| `HOLDER_DISTRIBUTED` | holders | `top_10_pct < 30` | +5 | Supply is broadly distributed. |
| `LP_LOCKED` | liquidity | `locked_pct > 90` | +5 | Liquidity is locked. |
| `DEV_REPUTABLE` | deployer | `dev.risk_score < 20` | +5 | Deployer has a clean history. |
| `VOLUME_AUTHENTIC` | market | `fake_volume_pct < 10` | +5 | Volume looks organic. |
| security check id (e.g. `CR01`) | security | check `status == "pass"` | +2 | Individual security check passed. |

### 2.3 Security check matrix (42 checks, GoPlus + token_security)

The `token_security` DataBus chain runs 42 checks (`SECURITY_CHECKS` in
`app/databus/token_security.py`). In the unified engine each **fail** is −5,
each **pass** +2; the check id becomes the flag. The underlying
`TokenSecurityScorer` additionally computes a weighted 0–100 "Authentic
Score" with 8 auto-fail checks (CR01, CR03, CR05, HP01, HP02, LR01, RP01,
RP02) forcing ≥ 85.

| ID | Check | Category | Auto-fail | Meaning |
|---|---|---|---|---|
| `CR01` | Contract Verified | contract_risks | ✅ | Source code verified on the explorer. |
| `CR02` | Proxy Contract | contract_risks | | Upgradeable proxy — owner can change logic. |
| `CR03` | Mint Function | contract_risks | ✅ | Unrestricted `mint()` — infinite supply possible. |
| `CR04` | Owner Privileges | contract_risks | | Owner can pause, blacklist, or adjust fees. |
| `CR05` | Self-Destruct | contract_risks | ✅ | Contract contains `SELFDESTRUCT`. |
| `CR06` | External Call Risk | contract_risks | | Contract makes risky external calls. |
| `CR07` | Delegatecall Risk | contract_risks | | Contract uses `DELEGATECALL`. |
| `HP01` | Honeypot (GoPlus) | honeypot | ✅ | GoPlus reports the token cannot be sold. |
| `HP02` | Honeypot (Honeypot.is) | honeypot | ✅ | Honeypot.is simulation failed. |
| `HP03` | Sell Tax Anomaly | honeypot | | Sell tax > 10% (fail > 50%). |
| `HP04` | Transfer Pausability | honeypot | | Transfers can be paused. |
| `HP05` | Max Transaction Limit | honeypot | | Max transfer amount cap. |
| `LR01` | Liquidity Locked | liquidity | ✅ | LP tokens are locked. |
| `LR02` | Liquidity Amount | liquidity | | Pool has meaningful liquidity. |
| `LR03` | LP Holder Concentration | liquidity | | A few wallets control most LP. |
| `LR04` | Liquidity/Supply Ratio | liquidity | | Liquidity is small relative to supply. |
| `LR05` | Creator LP Removal | liquidity | | Creator has removed LP before. |
| `HD01` | Top 10 Concentration | holders | | Top 10 wallets hold too much. |
| `HD02` | Creator Holdings | holders | | Creator holds a large share. |
| `HD03` | Holder Count | holders | | Too few holders. |
| `HD04` | Whale Dominance | holders | | Whales dominate supply. |
| `HD05` | Fresh Wallet % | holders | | High share of brand-new wallets. |
| `TA01` | Wash Trading % | volume | | Wash trading detected. |
| `TA02` | Volume/Liquidity Ratio | volume | | Volume is implausibly high vs liquidity. |
| `TA03` | Buy/Sell Imbalance | volume | | Lopsided buy/sell ratio. |
| `TA04` | Trade Count | volume | | Trade count inconsistent with activity. |
| `TA05` | Price Impact | volume | | Large price impact per trade. |
| `DT01` | Deployer History | deployer | | Deployer history is risky. |
| `DT02` | Deployer Funding | deployer | | Funding source is suspicious. |
| `DT03` | Multi-Token Deployer | deployer | | Deployer shipped many tokens. |
| `DT04` | Team Wallet Tracing | deployer | | Team wallets interlinked. |
| `DT05` | Social Presence | deployer | | No credible social presence. |
| `TE01` | Supply Concentration | taxes | | Supply concentrated at top wallets. |
| `TE02` | Tax Anomaly | taxes | | Buy/sell tax unusual. |
| `TE03` | Transfer Fee | taxes | | Fee on every transfer. |
| `TE04` | Blacklist Function | taxes | | Contract can blacklist addresses. |
| `TE05` | Max Wallet Cap | taxes | | Wallet balance cap. |
| `RP01` | Rug Pull — Known Pattern | rug | ✅ | Matches a known rug pattern. |
| `RP02` | Liquidity Removal | rug | ✅ | History of removing liquidity. |
| `RP03` | Price Crash | rug | | Historical price crash pattern. |
| `RP04` | Duplicate Token | rug | | Copy of an existing token. |
| `RP05` | Age Risk | rug | | Token is very young. |

### 2.4 Legacy flags

Emitted by the legacy path (`app/domains/scanner/service/scan.py`) from
module results, and used by the public scanner API and Telegram alerts.

| Flag | Detected by | Meaning |
|---|---|---|
| `honeypot` | module `is_honeypot` | Cannot sell. |
| `flagged_spam` | module `is_spam` | Token flagged as spam. |
| `sanctioned_address` | module `is_sanctioned` | Deployer/contract sanctioned. |
| `bundled_launch` | module `is_bundled` | Launch bundled with bots. |
| `copycat_token` | copycat `result == "high_risk"` | Copy of a known token. |
| `malicious_deployer` | `reputation_label == "CRITICAL"` | Deployer is a serial rugger. |
| `high_risk_deployer` | `reputation_label == "HIGH"` | Deployer is high risk. |
| `high_risk_score` | goplus/honeypot `risk_score >= 70` | Provider risk score critical. |
| `concentrated_holders` | `concentration_pct > 80` | Holders concentrated. |
| `new_token_under_1h` | token_age `risk_level == "critical"` | Token is under 1 hour old. |
| `token_under_24h` | token_age `is_new_token` | Token is under 24 hours old. |
| `extreme_lp_concentration` | lp_concentration `risk_level == "critical"` | LP extremely concentrated. |
| `concentrated_lp` | lp_concentration `is_concentrated` | LP concentrated. |
| `active_proxy_owner` | ownership `active_proxy_owner` | Owner controls upgradeable contract. |
| `active_owner` | ownership `active_owner` | Owner retains control. |
| `suspicious_social_sentiment` | social_sentiment `is_suspicious` | Social hype looks manufactured. |
| `wash_trading_suspected` | volume_liquidity_ratio `is_wash_trading_suspicious` | Wash trading suspected. |
| `extreme_volume_liquidity_ratio` | volume_liquidity_ratio `risk_level == "critical"` | Volume/liq ratio extreme. |

### 2.5 SENTINEL red flags

SENTINEL collects red flags per module via
`_collect_red_flags(report_dict, module_name)`: up to 5 items from each
module's `red_flags`/`warnings`/`flags`/`risks` lists, prefixed
`[module] <text>`, plus `[module] risk_level=high|critical`. Example:
`[liquidity_verification] LP tokens not locked`.

---

## 3. Explainability

Every unified score can be traced back to specific findings via two payloads
attached to the scan result:

- `breakdown` — per-dimension points, deltas and flags.
- `evidence` — the raw findings that produced the flags.

Both are populated by `compute_score()` and stored on `ScanResult` as
`breakdown` and `evidence`; `scan_token_unified()` includes them in the
legacy-shaped dict returned to consumers.

### Breakdown schema

```json
{
  "score": 29.0,
  "risk_level": "high",
  "breakdown": {
    "security":  {"score": 34.0, "max": 35, "delta": -1.0,  "flags": ["HP01"]},
    "liquidity": {"score": 5.0,  "max": 25, "delta": -20.0, "flags": ["LP_LOCK_LOW"]},
    "holders":   {"score": 15.0, "max": 15, "delta": 0.0,   "flags": []},
    "deployer":  {"score": 15.0, "max": 15, "delta": 0.0,   "flags": []},
    "market":    {"score": 10.0, "max": 10, "delta": 0.0,   "flags": []}
  }
}
```

- `score` — surviving points in the dimension: `clamp(0, max, max + delta)`.
- `max` — the dimension weight (35/25/15/15/10).
- `delta` — net contribution to the final score.
- `flags` — red flags that caused the penalties.

**Verification arithmetic:** `score = clamp(0, 100, 50 + Σ delta)`
= `50 + (-1) + (-20) + 0 + 0 + 0` = **29** ✅. `risk_level` = `high`
(20 ≤ 29 < 40) ✅.

### Evidence schema

`evidence` mirrors the breakdown keys and holds the raw findings:

```json
{
  "security": {
    "checks": {"HP01": {"status": "fail", "details": "GoPlus honeypot=YES"}},
    "score": 61.2,
    "band": "HIGH RISK",
    "rug_patterns": {"match_count": 0, "patterns": []}
  },
  "liquidity": {"locked_pct": 0, "liquidity_usd": 45000},
  "holders": {"top_10_pct": 35.0, "holder_count": 1200},
  "holder_health": {"gini": 0.62},
  "deployer": {"deployer_address": "0xdev...", "risk_score": 55, "risk_label": "HIGH"},
  "market": {"fake_volume_pct": 22}
}
```

### Worked example: critical token

Real output from `compute_score()` on a token with honeypot, unlocked LP,
concentrated holders, repeat-rug deployer and fake volume:

```json
{
  "score": 0.0,
  "risk_level": "critical",
  "breakdown": {
    "security":  {"score": 0.0, "max": 35, "delta": -40.0, "flags": ["HP01", "CR01", "CR03", "TA01", "RUG_PATTERN_MATCH:2"]},
    "liquidity": {"score": 5.0, "max": 25, "delta": -20.0, "flags": ["LP_LOCK_LOW"]},
    "holders":   {"score": 0.0, "max": 15, "delta": -25.0, "flags": ["HOLDER_CONCENTRATION_HIGH", "HOLDER_GINI_HIGH"]},
    "deployer":  {"score": 0.0, "max": 15, "delta": -25.0, "flags": ["DEV_HIGH_RISK"]},
    "market":    {"score": 0.0, "max": 10, "delta": -70.0, "flags": ["VOLUME_FAKE", "ONCHAIN_BUNDLER", "PREMIUM_BUNDLE_DETECT", "PREMIUM_WASH_TRADE_DETECT"]}
  },
  "evidence": {
    "security": {
      "checks": {"HP01": {"status": "fail", "details": "GoPlus honeypot=YES"}, "CR01": {"status": "fail", "details": "Verified=NO"}, "CR03": {"status": "fail", "details": "Mintable=YES"}, "TA01": {"status": "fail", "details": "Wash trading detected"}},
      "score": 92.5,
      "band": "DANGER",
      "rug_patterns": {"match_count": 2, "patterns": ["RP01 Rug Pull - Known Pattern", "RP02 Liquidity Removal"]}
    },
    "liquidity": {"locked_pct": 0, "liquidity_usd": 12000},
    "holders": {"top_10_pct": 92.4, "holder_count": 421},
    "holder_health": {"gini": 0.91, "hhi": 4100},
    "deployer": {"deployer_address": "0xdev...", "risk_score": 88, "risk_label": "CRITICAL", "rug_count": 9, "total_deployments": 12},
    "market": {"fake_volume_pct": 81, "premium": {"bundle_detect": {"risk": "HIGH"}, "wash_trade_detect": {"risk": "CRITICAL"}}}
  }
}
```

**Reading it:** 4 failed security checks (−20) + 2 rug-pattern matches (−20)
→ security −40; unlocked LP → liquidity −20; 92% top-10 + gini 0.91 → holders
−25; deployer risk 88 → deployer −25; fake volume 81% (−20) + chain-indexer
bundler finding risk 30 (−30) + bundle-detect premium (−10) + wash-trade
premium (−10) → market −70. Total `50 − 180` clamps to **0** → `critical`.

### How to trace any score

1. Start at the baseline **50**.
2. For each dimension, read `delta` — the net points lost/gained there.
3. For each negative delta, read `flags` in that dimension, then look up the
   same flags in `evidence` to see the raw check/finding (status, details,
   percentages).
4. Sum the deltas, clamp to 0–100, and map to the risk level table.

### Confidence

`confidence` (0–100) measures data coverage, not safety: +5 per attempted
module (cap 40) plus +5–+15 for modules that returned data. A score with low
confidence means the data available was thin — treat it accordingly.

---

## 4. Implementation

| Concern | Location |
|---|---|
| Canonical score + breakdown + evidence | `app/domains/scanner/unified/scoring.py` |
| Scan orchestration (phases, tier gating) | `app/domains/scanner/unified/scanner.py` |
| Result model (`ScanResult`) | `app/domains/scanner/unified/model.py` |
| Persistence / RugBrain RAG / Redis | `app/domains/scanner/unified/persistence.py` |
| Legacy module-results score | `app/domains/scanner/service/scoring.py` |
| Legacy risk-flag generation | `app/domains/scanner/service/scan.py` |
| Security check matrix (42 checks) | `app/databus/token_security.py` |
| SENTINEL composite + red flags | `app/domains/scanners/sentinel_pipeline/risk.py` |

### Change log

- **2026-08-03** — Documented the 7 clever-wallet holder penalties that were
  missing from the unified penalty table: `HIGH_EXCHANGE_FUNDED`,
  `EXCHANGE_FUNDED_ELEVATED`, `HIGH_FRESH_WALLET_PCT`,
  `FRESH_WALLET_ELEVATED`, `MIXER_FUNDED_HOLDERS`, `HOLDER_OVERLAP_SCAM`,
  `HOLDER_OVERLAP_HIGH` (from `funding_analysis`, `wallet_freshness`,
  `holder_overlap` in `compute_score()`). Added them to the unified penalty
  table, the flag glossary, the holders dimension summary, and a trigger
  note. Auto-fail count verified at 8 (CR01, CR03, CR05, HP01, HP02, LR01,
  RP01, RP02) — already correct.
- **2026-08-03** — `compute_score()` now returns an explainable
  `{score, risk_level, breakdown, evidence}` payload; `ScanResult` gains
  `breakdown` and `evidence`; `scan_token_unified()` includes both in the
  returned dict. All penalty/bonus amounts and thresholds are unchanged —
  this is a pure explainability refactor.
