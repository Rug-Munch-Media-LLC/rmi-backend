# PAID-FEATURES-PLAN.md — Monetizable Feature Roadmap

> Status: planning · READ-ONLY analysis (2026-08-13) · Owner: Rug Munch Media LLC Engineering
> Scope: rmi-backend (x402 billing, scanners, RAG, bot) + rugmunch-scanner (scan_token_complete, lineage, cohorts, rugcheck, alerts) + lite-chain-indexer (launch detection, Parquet/DuckDB).
> Every feature reuses existing rails: `app/domains/billing/x402` (X402Enforcer, TOOL_PRICES, trial, subscriptions, checkout, settle) — no new payment plumbing.

---

## Ranked by revenue ÷ effort

| # | Feature | Est. MRR/unit | Effort | Data source | Rail |
|---|---------|--------------|--------|-------------|------|
| 1 | Real-time new-launch alerts | $19–49/mo recurring | **Low** (2–3 wk) | lite-chain-indexer `strategies/solana/launch.py` `detect_launch()` → NATS | Subscription + Telegram |
| 2 | Smart-money copy signals | $29–79/mo | **Med** (3–4 wk) | rugmunch-scanner `cohorts/engine.py` + rmi-backend `smart_money_tracker` | Subscription + x402/alert |
| 3 | Wallet/portfolio risk monitor | $9–19/mo/wallet | **Med** (3 wk) | `unified_scanner`, `portfolio_risk_aggregator`, `websocket_alerts` | Subscription |
| 4 | Scanner data API tier (DuckDB/Parquet) | $99–499/mo | **High** (4–6 wk) | lite-chain-indexer `storage/parquet.py` + `storage/duckdb_query.py::query_parquet` | API keys + x402 |
| 5 | Deep forensic reports (lineage + fund-flow) | $25–100 one-off | **Med** (3 wk) | rugmunch-scanner `lineage/assembler.py`, `forensics/fund_tracer.py`, RAG `ingest_forensic_report` | x402 pay-per-report |

---

## 1. Real-time New-Launch Alerts (Pump.fun / Raydium / Uniswap) — TOP PRIORITY

**Data:** lite-chain-indexer already detects launches in-stream (`detect_launch()`: pump.fun create / Bonk.fun / LaunchLab create, PumpSwap + Raydium graduation, `LaunchEvent` with mint, creator, venue, confidence, ISO timestamp) and publishes via `streaming/nats_publisher.py`. **The detection is built; the monetization is not wired.**

**Pricing:** $19/mo Starter (pump.fun only, ≤50 alerts/day, 5-min delay); $49/mo Pro (all venues, Raydium graduation, bundler-filtered, <30s, priority queue). x402 $0.10/alert pay-as-you-go fallback for non-subscribers.

**Wiring:**
- Add a NATS consumer in rmi-backend (`app/workers/`) subscribing to the indexer's launch subject → filter by venue/confidence → dedupe → push to existing `alert_pipeline.push_alert()` (Redis) + `websocket_alerts.AlertBroadcaster`.
- New `app/api/v1/x402/x402_launch_alerts.py` router: `POST /alerts/subscribe` (creates subscription via billing `subscriptions`), `POST /alerts/rule` (filters), gated by `X402Enforcer` + `tier_resolver.check_burst_limit`.
- Telegram delivery: reuse rugmunch-scanner `alerts/notifiers.py::TelegramNotifier` (already rate-limited via TokenBucket + daily counter) — bot command `/launch-alert` to subscribe with wallet/venue filters.
- Frontend: subscription page in rmi-frontend; delivery is Telegram-native so MVP needs no web UI.

**Effort:** Low. Indexer side done; ~80% is billing-gate + NATS consumer + notifier routing. First revenue within weeks.

---

## 2. Smart-Money Copy Signals

**Data:** rugmunch-scanner `cohorts/engine.py` scores wallets (sharpe_30d, max_drawdown, activity, PnL attribution) → top-percentile wallets; rmi-backend `smart_money_tracker/tracker.py` produces `DetectedMove` events (direction, `_score_impact`, USD value) for those wallets. Both exist.

**Pricing:** $29/mo signals-only (per-trade alert); $79/mo + cohort rankings API + position context. x402 $0.50/signal on-demand.

**Wiring:**
- Cron: cohorts engine refreshes weekly → seeds smart_money_tracker wallet list (`add_wallet`) → tracker scan emits moves → new `signal_pipeline` merges move + cohort score → alert channels (existing notifiers) + `/api/v1/x402/x402_alpha_tools.py` enrichment.
- Subscription gate identical to Feature 1; signal history stored in Postgres for the $79 tier.

**Effort:** Med. Tracker + cohorts already production-shaped; needs join layer + billing + delivery.

---

## 3. Wallet / Portfolio Risk Monitor

**Data:** rmi-backend `unified_scanner` (WalletRiskFactors, Helius/Moralis/Etherscan providers), `portfolio_risk_aggregator`, `rug_imminence_predictor`, rugmunch-scanner `rugcheck` (EVM+Solana on-chain checks) + `alerts/storage.py`. `websocket_alerts` already supports per-wallet subscription (`subscribe(client_id, wallet_address)`).

**Pricing:** $9/mo per wallet (2 wallets), $19/mo 10 wallets + Telegram push. Free tier: 1 wallet, 24h polling.

**Wiring:**
- Add `watchlist` table (user_id, wallet, chains, thresholds) + `/api/v1/x402/x402_wallet_monitor.py`: `PUT /monitor/wallet`, `GET /monitor/summary` (risk score, breaches), `POST /monitor/alert`.
- Background worker polls unified_scanner on 15-min interval (free) / 1-min (paid), pushes breaches via websocket broadcaster + Telegram notifier.
- Frontend: reuse existing wallet pages; add "monitor" toggle + billing.

**Effort:** Med. Scaffolding exists (websocket_alerts per-wallet, unified_scanner); missing persistence + billing gate.

---

## 4. Scanner Data API Tier (DuckDB/Parquet-backed)

**Data:** lite-chain-indexer daily Parquet exports (`storage/parquet.py::ParquetExporter`) + `storage/duckdb_query.py::query_parquet`/`query_httpfs` (views over transfers, DEX trades, traces) + `api/routers/` (transfers, whales, deployers, search) + rugmunch-scanner `scan_token_complete` for on-demand enrichment.

**Pricing:** $99/mo Developer (50k rows/query, 100 req/day, SQL over last 7d); $299/mo Pro (full history, 500 req/day, webhooks); $499/mo Institutional (dedicated rate, cohort + lineage joins). x402 per-query fallback ($0.01/1k rows).

**Wiring:**
- New `app/api/v1/data/` router wrapping `query_parquet` + scanner enrichment — one SQL endpoint (`POST /data/query`) + typed endpoints (transfers, launches, whales) proxying indexer `api/routers`.
- Extend existing `check_developer_key` / `tier_resolver` into a real key service (issue, rotate, revoke, quota counters in Redis).
- Docs + OpenAPI export; portal page for key management.

**Effort:** High (key infra + quota + SQL surface), but monetizes zero-marginal-cost data we already generate; highest ceiling.

---

## 5. Deep Forensic Reports (Lineage + Fund-Flow)

**Data:** rugmunch-scanner `lineage/assembler.py::assemble_lineage` (deployer, funder depth, bundle ID, shared funder, initial liquidity, graduation, prior-launch count) + `forensics/fund_tracer.py` (trace_forward/backward, cross-chain paths, bridge usage, CEX deposits) + rmi-backend `cross_chain_trace` + RAG `ingest_forensic_report` (persists report for retrieval).

**Pricing:** x402 pay-per-report: $25 Standard (lineage + rugcheck + labels), $60 Advanced (+ fund-flow graph, bridge/CEX hops), $100 Investigative (cross-chain full trace + RAG narrative via `investigation_narratives.py`).

**Wiring:**
- `POST /api/v1/x402/x402_forensic_tools.py` (exists) extended to orchestrate: lineage → fund_tracer → narrative → rendered PDF/HTML → email/delivery. x402 checkout already handles payment + settlement.
- Report cache in Redis (idempotent re-download); compliance note.

**Effort:** Med. All analysis modules exist and some x402 forensic tools ship; gap is the orchestrated report bundle + delivery.

---

## Sequencing & Dependencies

1. **Sprint A (wk 1–3):** Feature 1 (launch alerts) — fastest revenue, reuses NATS + alert_pipeline + TelegramNotifier + subscription router.
2. **Sprint B (wk 3–6):** Feature 3 (wallet monitor) + Feature 2 (signals) — both reuse subscription rail built in Sprint A.
3. **Sprint C (wk 6–9):** Feature 5 (forensic reports) — pure x402, no subscription.
4. **Sprint D (wk 9–15):** Feature 4 (API tier) — key infra; highest revenue but needs quota/keys.

Shared groundwork: billing subscription model must support both x402 pay-per-call AND recurring monthly (currently x402 is pay-per-call + trials — `x402_subscriptions.py` exists but is thin). Frontend checkout page for subscriptions is the one cross-cutting dependency.

## Risks / Flags
- [REVIEW] Launch alerts compete with existing pump.fun tracking bots; differentiation = bundler-filtering + lineage scoring. Verify alert latency budget (<30s) against NATS + Redis path.
- [REVIEW] Smart-money signal quality unproven — cohort Sharpe is derived, not backtested. Ship as beta with trial first.
- [REVIEW] API tier SQL surface = data-exfiltration risk; must sandbox queries (read-only DuckDB, row caps, no arbitrary SQL on raw exports).
- [REVIEW] Subscription billing (recurring USDC) needs autopay/approval UX — x402 is designed for one-shot; verify `x402_subscriptions.py` maturity before Sprint A commit.
- [REVIEW] Wallet monitor latency vs provider rate limits (Helius 25k req/mo free tier) — per-wallet polling costs scale linearly; cap free tier accordingly.
