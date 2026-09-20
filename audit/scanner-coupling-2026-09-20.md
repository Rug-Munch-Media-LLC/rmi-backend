# rmi-backend ↔ rugmunch-scanner coupling audit (2026-09-20)
Agent: explore-ds (read-only). Companion: rugmunch-scanner/audit/standalone-audit-2026-09-20.md, /root/.knowledge/audits/rugmunchbot-audit-2026-09-20.md.

## 1. Scanner code inside backend (real layout)
- `app/scanners/` DOES NOT EXIST. Actual:
  - `app/domains/scanner/` (singular) — 213 .py — canonical target `rugmunch_scanner/unified/`
  - `app/domains/scanners/` (plural) — ~40 .py — canonical target `rugmunch_scanner/detectors/`
  - `app/scoring/`, `app/wallet_memory/` — already thin adapters (PROVENANCE Phases 2–3)
  - Glue: `app/token_scanner.py`, `app/clone_scanner.py`, `app/wallet_drain_scanner.py`, `app/trufflehog_scanner.py`, `app/unified_token_scanner.py`, `app/unified_scanner/`, `app/detectors/`, `app/analyzer/`
- Duplication measured: `honeypot_detector.py` 459 loc (backend) vs 471 (scanner), diff = import rewrites only. Same for bundle_detector (664 vs copy).
- Only 3 of 56 plural detectors are thin adapters (contract_authority, oracle_manipulation, proxy_detector). 53 remain full backend implementations.
- Direct `rugmunch_scanner` imports in backend: `app/routers/unified_scanner_router.py:102-103`, `app/wallet_memory/*` (7), thin adapters in domains/scanners/*, guarded imports in domains/scanner/unified/_phase_helpers.py:27, engines/risk_cluster/_builders.py:351, engines/_io.py:26.

## 2. Shared data stores
- ClickHouse `wallet_memory.*`: deployer_events, deployer_history, dex_events, evm_launches, evm_transfers, holder_balances, scam_addresses, solana_bundles, solana_launches, solana_spl_balance_changes, solana_transfers, token_meta, token_meta_ref, token_transfers, wallet_labels, token_prices.
- Scanner-only: wallet_memory.evm_holders, solana_holders, tron_launches, wallet_entities, wallet_profiles; own DB rugmunch_scanner.{smart_money,wallet_scores,watch_rules,subscriptions,alert_log}.
- Shared Redis: sentinelscan:{chain}:{address}, bundle_analysis:own, event_correlation:own, first_buyers:own, risk_report:structured, news_card:rag, news_sentiment:rag, rmi:webhooks, rmi:webhook_deliveries, sentinel:free, sentinel:premium, wallet_analysis:own.

## 3. Consumption points (file:line)
- worker.py:86/:94 — Redis token_scan_queue consumer (USE_UNIFIED_SCANNER flag at :34)
- app/routers/unified_scanner_router.py (707 loc) — /api/v2/scanner/token/scan, /wallet/scan
- app/routers/scanner_data_router.py — 5 cached routes reading sentinelscan:*
- app/api/v1/public/scanner.py:138 — POST /api/v1/scanner/scan
- app/features.py:13,19 — DI composition root
- app/lifespan.py:140 WebhookEngine; :298 rug-learning loop
- app/databus/token_launch_detector.py:473,512,563 — HoneypotDetector/LiquidityVerifier/BundleDetector
- app/databus/providers/chains/lite_chain_indexer_own.py — 11 scanner-engine imports
- app/domains/billing/x402/tools/{evidence,report,label}_tools.py — 20 sentinel_pipeline imports
- app/domains/detectors/rug_imminence_predictor* — 8 plural-detector imports
- app/domains/telegram/alert_hooks.py — send_scanner_alert called from scanner/service/scan.py:250, scanner/unified/scanner.py:521, scanners/sentinel_pipeline/runners.py:482, engines/new_token_radar.py:497, engines/attack_detector.py:426
- app/mount.py:211,212,284,287,309 — router mounts
- app/agents/scanner.py:23-52 — HTTP client to SCANNER_API_URL (already decoupled)

## 4. KEEP / MOVE / DELETE
- KEEP (thin glue): app/features.py, unified_scanner_router.py, scanner_data_router.py, public scanner API, mount.py, lifespan webhook wiring, agents/scanner.py.
- MOVE to scanner: 53 remaining domains/scanners/* impls → rugmunch_scanner/detectors/; 182+ domains/scanner/* → rugmunch_scanner/unified/; top-level token_scanner.py, clone_scanner.py, wallet_drain_scanner.py, trufflehog_scanner.py, unified_token_scanner.py, unified_scanner/, detectors/, analyzer/.
- DELETE after importer count hits 0: duplicate detectors, engines/_io.py + risk_cluster/_builders.py + unified/_phase_helpers.py adapters; telegram/rugmunchbot/ leftovers (pycache + data/bot_users.db only; commit 6a5b5e6d already deleted the code).

## 5. Bot side (backend)
- `app/telegram_bot/` DOES NOT EXIST. Bot logic at app/domains/telegram/ (62 .py): adminbot/ (stays), rugmunchbot/ (deleted, stale artifacts only), scanner_bridge.py, alert_hooks.py, access_guard.py, headless*, voice_chat.py, secure_launcher.py, router.py.
- Standalone rugmunchbot repo is the live home (see bot audit).

## 6. Deployment glue
- docker-compose.yml:254 rmi-backend, :303 rmi-worker (profile full) — worker runs scanner queue.
- `app/domains/telegram/rugmunchbot.service` — STALE: WorkingDirectory/ExecStart point at rmi-backend; bot is standalone now.
- infra/attack-detection.service+.timer, infra/deployer-tracker.service+.timer, infra/download-labels.service+.timer.
- Env flags: USE_UNIFIED_SCANNER, WEBHOOK_ENGINE_ENABLED, SCANNER_ALERTS_ENABLED, PREFER_OWN_DATA, SCANNER_API_URL, SCANNER_API_KEY.

## 7. Scanner repo's own `app.*` leaks (50 sites)
unified/_ports.py is the audited seam; remaining: unified/service/scoring.py:128, unified/_data_sources.py:157, _finalize_helpers.py:202, rag_scam_check.py:196, shared/deployer_labeler.py:67,125, tracing/receipt.py:164, engines/hype_score.py:237, news_sentiment.py:270,540, event_correlation.py:180,253, _price_adapters.py:92,212,288,324,375, webhooks/providers/*, modules/social/*, modules/{holders,deployer,security}.py, rugmaps/graph.py:191,232,244, detectors/_compat.py:223,235, detectors/_fraud_gnn.py:33.
