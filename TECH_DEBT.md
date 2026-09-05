# Tech Debt Registry

Tracked markers use the `# FIXME(issue): <description>` convention in source.
Each entry below maps to a source marker. Resolve the code, then delete both
the marker and its entry here.

Last audited: 2026-09-05 (43 raw grep hits; 30 were false positives — content
strings like "HACK ALERT", constants like `ETHERSCAN_PHISH_HACK` /
`HACKING_PATTERNS`, and `MASTODON` which contains the substring "TODO").

## High Priority

| # | File | Description |
|---|------|-------------|
| 1 | `app/routers/security_intel.py:255` | Endpoints call APIs that do not match current class surfaces (`ml_anomaly`, `CrossChainCorrelator`, `TokenMetricAnomalyDetector`, `SentinelManager`) — would raise `AttributeError` at runtime. Rework against actual APIs. |
| 2 | `app/agents/tools.py:23` | `ScanTokenTool.execute` returns a stub (`status: pending integration`). Wire to the actual rug-pull scanner. |
| 3 | `app/domains/postiz/services/store.py:15` | `PostizStore` is in-memory — data lost on restart. Replace with Postgres + SQLAlchemy models + Alembic migrations (pattern: `app/domains/analytics/`). |
| 4 | `app/detectors/flash_loan/algorithms/__init__.py:339` | USD value estimation is simplified/stubbed. Replace with DataBus / `price_consensus` module call for production. |
| 5 | `app/mcp/server.py:190` | `analytics_query` (TIER 2 moat tool, read-only DuckDB SQL) has no API key check — add one before opening to public (M3 moat). |

## Medium Priority

| # | File | Description |
|---|------|-------------|
| 6 | `app/contract_deepscan.py:22` | Migrate `_fetch_source_code` to async `httpx.AsyncClient`. |
| 7 | `app/integrations/blockchair_connector.py:23` | Migrate to async `httpx.AsyncClient` — class uses sync `httpx.get()`. |
| 8 | `app/integrations/defillama_connector.py:23` | Migrate to async `httpx.AsyncClient` — class uses sync `httpx.get()`. |
| 9 | `app/integrations/nansen_connector.py:24` | Migrate to async `httpx.AsyncClient` — class uses sync `httpx.get()`. |
| 10 | `app/routers/bulletin_board/admin.py:403` | Implement comment moderation in `bulletin_board.py` (admin endpoint currently only audits the action). |
| 11 | `app/api/v1/auth/alerts.py:46` | `list_alerts` is a stub returning an empty list — wire up to Postgres once auth context is established. |
| 12 | `app/ml_datasets/adapters.py:16` | Replace hash-synthesis feature stub with real on-chain enrichment once the `address -> FRAUD_FEATURES` builder exists. |
| 13 | `app/domains/labels/sources/internal_labels.py:6` | Query Postgres `internal_labels` table once the schema lands (currently returns `[]`). |

## Low Priority

| # | File | Description |
|---|------|-------------|
| 14 | `app/cross_chain_tracking/entity_tracker.py:47-50` | Entity resolution expansions: EVM address portability, bridge mapping from `wallet_memory`, behavioral fingerprinting, fund flow tracing. |
| 15 | `app/domains/labels/sources/chainbase.py:3` | Add `CHAINBASE_API_KEY` to secrets manager + env (free tier: 50K calls/month); source is a stub. |
| 16 | `app/domains/labels/sources/open_labels.py:3` | Wire up Open Labels Initiative (OLI) source once the endpoint is confirmed. |
| 17 | `app/domains/newsletter/__init__.py` | Newsletter router (`app/domains/newsletter/router.py`, 15 routes) is built but not mounted on the main app at `/api/v1/newsletter/*`. |

## Resolved During This Audit

- `app/domains/newsletter/__init__.py` — `[TODO]` tags on `service.py` and
  `router.py` removed: `NewsletterService` now lives in
  `app/domains/newsletter/services/` and `router.py` exists with 15 routes.
  Remaining work (mounting the router) is tracked as item #17 above.
