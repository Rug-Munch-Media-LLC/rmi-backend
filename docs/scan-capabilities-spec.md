# RugMunchScan Capabilities Spec

> Status: proposed · Owner: Rug Munch Media Engineering · Source: architect design (scan capabilities)
> Repo: rmi-backend · Last updated: 2026-08-02

This document specifies the RugMunchScan capability set: the next-generation scanning and
intelligence features that replace third-party tools (Arkham, BubbleMaps, etc.) with
in-house analysis built on our own data.

## Table of Contents

1. [Foundations (F0-F2)](#f0-f2-foundations-p0)
2. [Capabilities (C1-C7)](#capabilities-c1-c7)
3. [Build Order & Sequencing](#build-order--sequencing)
4. [Cross-Cutting Principles](#cross-cutting-principles)

---

## Foundations (F0-F2) (P0)

Foundations are prerequisite data fixes. No capability work starts until the foundations
are in place — every downstream feature consumes their output.

### F0: Deployer EOA Fix

**Purpose:** Capture the *real* deployer address on token creation. Current `deployer_events`
is unreliable because it records the contract (or factory) address rather than the
externally owned account (EOA) that submitted the deployment transaction. This fix makes
deployer attribution correct for every capability that keys on "who launched this token".

**Data sources:** `deployer_events` (existing), raw EVM transfer/trace data, chain RPCs.

**Algorithm sketch:**
1. For each deployment event, resolve the `from` address of the transaction that created the
   contract (transaction origin, not the factory).
2. Persist the EOA deployer address alongside the existing event.
3. Capture `token0` / `token1` at deployment time so the pair's asset composition is known
   without a later, second lookup.
4. Backfill historical rows where the EOA is recoverable from traces; flag the rest for
   re-scan on next observation.

**Outputs:**
- Corrected `deployer_events` rows: `deployer_eoa`, `token0`, `token1`, `pair_address`, `block`, `tx_hash`.
- Backfill migration report (rows repaired vs. rows unresolvable).

**Complexity:** Low-Medium. Mostly trace parsing and one schema migration.

**Priority:** P0 — foundation for C3, C7, C4.

**Acceptance criteria:**
- 100% of new deployment events carry a valid EOA deployer.
- `token0` / `token1` populated on ≥95% of new events.
- Backfill completes without data loss; unresolvable rows explicitly listed.
- No downstream reader depends on the old contract-as-deployer behavior.

---

### F1: Solana Owner Resolution

**Purpose:** Add ownership resolution to Solana token balance changes. Today
`solana_spl_balance_changes` records *who holds what* but not *who owns the token account*.
Ownership (the account's authority) is required for holder clustering, entity resolution,
and ownership-based risk signals.

**Data sources:** `solana_spl_balance_changes` (existing), Solana RPC (getTokenAccountsByOwner /
account info), Helius (free tier).

**Algorithm sketch:**
1. Add an `owner` column to `solana_spl_balance_changes`.
2. Resolve the owning wallet from the token account's authority at observation time.
3. Maintain a cached account → owner mapping; refresh on balance changes and periodically.

**Outputs:**
- `owner` populated on balance change rows.
- Cache freshness report (stale accounts, refresh latency).

**Complexity:** Low-Medium. Schema change + one lookup path.

**Priority:** P0 — foundation for C2, C4, C5.

**Acceptance criteria:**
- ≥98% of new Solana balance-change rows carry a resolved `owner`.
- Owner resolution adds no more than X ms latency to ingestion (budget set at implementation).
- Cache staleness surfaced via metrics, not silent.

---

### F2: Price/Decimals Oracle

**Purpose:** Build a USD normalization layer so every detector and output can express value in
USD. The oracle uses our own `dex_events` as the primary price source and free external APIs
only as enrichment/fallback. This is the single source of truth for "how much is this worth".

**Data sources:** `dex_events` (own, primary), free APIs (CoinGecko, DexScreener, GeckoTerminal — enrichment/fallback only).

**Algorithm sketch:**
1. Derive token → USD price from our own DEX swap/quote events (most recent, liquidity-aware).
2. Fall back to free APIs when own data is absent or stale.
3. Resolve token decimals from on-chain metadata; cache per-token.
4. Expose a uniform interface: `price_usd(token, block|timestamp)` and `normalize(amount, token) → usd`.
5. Time-bound all lookups so slow fallbacks cannot block scan loops.

**Outputs:**
- Oracle service/module with `price_usd` + `normalize` API.
- Price staleness and fallback-hit metrics.

**Complexity:** Medium. Requires consolidation of multiple price sources behind one interface.

**Priority:** P0 — foundation for C1, C2, C5, C6 (every USD-denominated output).

**Acceptance criteria:**
- All capability outputs are USD-normalized via the oracle; no detector hand-rolls USD math.
- Own-data price is preferred whenever fresh; external APIs used only as fallback.
- Price lookups never exceed the query time budget (see Cross-Cutting Principles).

---

## Capabilities (C1-C7)

### C1: Wallet-to-Wallet Flow Visualization (P0)

**Purpose:** Arkham replacement. Show labeled, USD-denominated fund flows between wallets so
investigations can answer "who sent money to whom, and how much" without leaving the platform.

**Data sources:** `evm_transfers` (own), `wallet_labels` (own), F2 oracle (USD normalization).

**Algorithm sketch:**
1. Build a directed graph from `evm_transfers` edges (sender → receiver).
2. Label nodes via `wallet_labels`; label edges by token and USD value via the oracle.
3. Restrict traversal to 1-2 hops to keep graphs legible and queries fast.
4. Aggregate multi-hop paths and surfaces (e.g., "funds passed through mixer-like address").

**Outputs:**
- Interactive graph payload: nodes (labeled wallets), edges (transfers with USD value), 1-2 hop paths.
- Aggregated flow summaries per entity.

**Complexity:** Medium. Graph query on large transfer table needs index + time-box strategy.

**Priority:** P0 — flagship investigative feature.

**Acceptance criteria:**
- 1-hop queries return labeled, USD-valued edges in target latency.
- 2-hop expansion available and time-boxed (no runaway traversals).
- Every edge carries a transfer tx hash (evidence output principle).

---

### C2: BubbleMaps-style Holder Clustering (P1)

**Purpose:** Show token holder concentration and how holders are connected — whether they share
a funder, deployed in the same block, or were bundled — and tag holders as CEX, team, or sniper.

**Data sources:** balance changes (EVM + Solana F1), `deployer_events` (F0), transfer history, DEX events.

**Algorithm sketch:**
1. Compute holder set + concentration metrics (top-N share, Gini-style index).
2. Cluster holders by shared attributes:
   - **Shared funder:** multiple holders funded by one wallet.
   - **Same block:** holders that first appeared in the same block (bundle/sybil signal).
   - **Bundle:** holders that transact in lockstep.
3. Classify clusters: CEX (known labels), team (deployer-linked), sniper (first-block buy patterns).
4. Emit cluster graph + per-cluster risk flags.

**Outputs:**
- Holder cluster map (BubbleMaps-style visualization payload).
- Concentration metrics and cluster risk flags.

**Complexity:** Medium-High. Clustering logic + classification heuristics.

**Priority:** P1 — enrichment/differentiator.

**Acceptance criteria:**
- Cluster detection runs per token within time budget.
- Every cluster carries the evidence (funder tx hash, block numbers, bundle tx hashes).
- CEX/team/sniper labels validated against known ground-truth samples.

---

### C3: New Token Discovery + Early Warning (P0)

**Purpose:** Detect new token deployments within seconds of creation, cross-reference the
deployer's fingerprint, auto-scan the new pair, and push an alert in under 60 seconds —
before the crowd arrives.

**Data sources:** `deployer_events` (polled, F0), deployer fingerprint (C7), scanner pipeline.

**Algorithm sketch:**
1. Poll `deployer_events` every 15-30s for new rows.
2. For each new deployer, look up the behavioral fingerprint (C7); score the deployment.
3. Auto-scan the new pair through the existing scanner pipeline (honeypot, LP, risk score).
4. If risk signals exceed threshold, push alert via `alert_pipeline` in <60s from first sighting.

**Outputs:**
- Real-time new-pair feed with deployer fingerprint overlay.
- Alerts: `alert_pipeline` events, each with tx hashes + risk score + fingerprint summary.

**Complexity:** Medium. Poll loop + orchestration of fingerprint lookup and auto-scan.

**Priority:** P0 — core "early warning" spine.

**Acceptance criteria:**
- New deployments detected within 30s of event availability.
- Alert-to-user latency <60s end to end.
- Every alert includes evidence: deployer, tx hash, fingerprint score, scan result.

---

### C4: Cross-Chain Entity Resolution (P1)

**Purpose:** Link the same actor across chains. A wallet on Ethereum, its deployer on BSC, and
a Solana account that shares a funder should resolve to one entity where the evidence
supports it. Also classify the entity: bridge user vs. launderer.

**Data sources:** all chain event tables, `deployer_events` (F0), Solana owners (F1), behavioral patterns.

**Algorithm sketch:**
1. Build candidate links: same deployer address across chains, same funding source, similar
   behavioral signatures (timing, velocity, interaction graphs).
2. Score candidate links by evidence strength; only link above threshold.
3. Classify resolved entities: **bridge** (funds cross via bridge contracts, coherent path) vs.
   **launderer** (tornado/mixer churn, peel chains, structuring).
4. Persist entity resolution; surface in wallet profiles and flow graphs.

**Outputs:**
- Resolved entity graph (cross-chain wallet links with evidence scores).
- Bridge vs. launderer classification with supporting patterns.

**Complexity:** High. Cross-chain matching + classification; depends on F0/F1.

**Priority:** P1 — depends on foundations and benefits from C1/C2 clustering inputs.

**Acceptance criteria:**
- Every resolved link carries evidence (shared deployer tx, shared funder tx, pattern match).
- Classification precision evaluated against labeled ground truth.
- Queryable from a single entity lookup API.

---

### C5: Hype Score (P1)

**Purpose:** A composite score of a token's momentum using free social signals and on-chain
velocity — a fast "is this thing heating up" number without paid sentiment APIs.

**Data sources:** CoinGecko trending (free), DexScreener (free), GeckoTerminal (free), own volume/holder growth (ClickHouse).

**Algorithm sketch:**
1. Collect component signals: trending rank, DEX volume growth, GeckoTerminal activity, our own
   volume velocity and holder-growth rate.
2. Normalize each signal to 0-100.
3. Combine into a weighted composite `hype_score` (weights tuned on historical correlation).
4. Emit score + per-signal breakdown so the number is explainable.

**Outputs:**
- `hype_score` (0-100) per token with signal breakdown.
- Trend history for score changes over time.

**Complexity:** Medium. Aggregation + weighting; free APIs only.

**Priority:** P1 — enrichment.

**Acceptance criteria:**
- Score recomputes on a fixed cadence within time budget.
- No paid API in the signal set (frugality).
- Breakdown explains score movement (which signal drove the change).

---

### C6: Time-Based Attack Detection (P0)

**Purpose:** Detect sandwich attacks, pump-and-dump schemes, and flash loan attacks directly
from our own `dex_events`. No external APIs required.

**Data sources:** `dex_events` (own; ~59M rows), F2 oracle for USD impact.

**Algorithm sketch:**
1. **Sandwich:** find attacker buy → victim tx → attacker sell patterns around a single block/swap;
   flag when attacker profit and price impact exceed thresholds.
2. **Pump-dump:** detect rapid price run-up from coordinated buys followed by distribution;
   requires volume/velocity time windows.
3. **Flash loan:** detect loan → swap sequence → repay within one transaction/block window.
4. Emit detector results with full evidence (tx hashes in, hashes out, profit, USD impact).

**Outputs:**
- Attack detection events: type, participants, tx hashes, USD impact, confidence.
- Feeds into `alert_pipeline` and token risk scores.

**Complexity:** High. Windowed analysis over 59M rows; demands strict time-boxing.

**Priority:** P0 — core differentiator, pure own-data.

**Acceptance criteria:**
- All three attack types detected on labeled/known attack samples.
- Every detection returns tx hashes (evidence output principle).
- Query execution time within budget on full `dex_events` table.

---

### C7: Deployer Behavioral Fingerprint (P0)

**Purpose:** Score every deployer 0-100 based on deployment history, success rate, funding
patterns, and risk-relevant behavior. This is the spine for early-warning triage (C3).

**Data sources:** `deployer_events` (F0), token performance history (own), funding/transfer data, wallet labels.

**Algorithm sketch:**
1. Compute per-deployer stats: number of deployments, success rate (surviving/active tokens),
   average token lifespan, funding sources and amounts, prior scam/honeypot flags.
2. Weighted risk model produces a **risk bucket** (low / medium / high / critical) and a
   0-100 **score** (higher = more risk).
3. Persist fingerprint per deployer; refresh on new deployments.

**Outputs:**
- Deployer fingerprint record: stats, risk bucket, 0-100 score, supporting evidence.
- Consumed by C3 to prioritize early-warning alerts.

**Complexity:** Medium. Aggregation + risk model; foundational for C3.

**Priority:** P0.

**Acceptance criteria:**
- Fingerprint exists for every deployer in `deployer_events`.
- Score correlates with ground-truth scam outcomes on historical samples.
- Every score change is explainable by the stat deltas.

---

## Build Order & Sequencing

Dependency-ordered execution. Do not skip ahead: later capabilities consume earlier outputs.

| Phase | Work | Rationale |
|-------|------|-----------|
| 1 | **F0 → F2** (foundations) | Correct deployer, Solana owners, and USD oracle before anything downstream. |
| 2 | **C7 → C3** (deployer → early warning spine) | Fingerprint feeds early-warning triage; together they form the flagship "spot the rug early" loop. |
| 3 | **C1 + C6** (parallel differentiators) | Independent of each other; both are own-data flagships (flow viz + attack detection). |
| 4 | **C2 + C5** (enrichment) | Holder clustering and hype score add depth on top of foundations and flows. |
| 5 | **C4** (entity resolution) | Cross-chain resolution benefits from every prior capability's labeled output. |

## Cross-Cutting Principles

1. **Own data first.** ClickHouse is the primary source for every capability. Free external
   APIs are enrichment only (F2 oracle, C5 signals). Never build a pipeline that depends on a
   paid or third-party source for core function.
2. **Port before build.** Roughly ~16.5K lines of existing detectors must be ported to
   ClickHouse-native queries before new logic is layered on. Reuse before rewrite.
3. **Time-boxed queries.** Every detector and graph traversal sets `max_execution_time` —
   especially on the large tables (264M-row transfers, 59M-row dex events). Slow queries are
   cancelled and surfaced, never allowed to block the scan loop.
4. **Evidence output.** Every detector returns tx hashes, not bare scores. A score without
   evidence is not shippable.
5. **Wire into existing rails.** New capabilities integrate with the existing
   `alert_pipeline`, `UnifiedWalletScanner`, and x402 tiers rather than inventing parallel
   delivery paths.
