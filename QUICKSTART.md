# RugMunchScan — Quickstart

> Get a token or wallet risk scan running in 5 minutes. Updated: 2026-08-03
> Full docs: [`SCANNER_STATUS.md`](SCANNER_STATUS.md) · [`docs/scan-capabilities-spec.md`](docs/scan-capabilities-spec.md) · [`docs/COMPETITIVE_LANDSCAPE.md`](docs/COMPETITIVE_LANDSCAPE.md) · [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)

## What It Does

RugMunchScan is the token/wallet risk scanning engine inside rmi-backend. It answers
two questions:

1. **"Is this token safe?"** — honeypot, liquidity, LP lock/burn, holders, deployer
   reputation, wash trading, bundle/sniper, copycat, attack patterns (sandwich,
   pump-dump, flash loan), and a composite `safety_score`.
2. **"Who is this wallet?"** — entity labels, risk score, scam-token exposure,
   behavioral persona, and cross-chain entity links (C4: same-deployer / same-funder
   across chains).

It scans **EVM chains (Ethereum, BSC, Arbitrum, Base, Polygon, and more) + Solana**,
and spans 4 engines:

| Engine | What It Is |
|---|---|
| `unified/scanner.py` | **UnifiedTokenScanner** — primary engine, DataBus-powered, own-data-first |
| `service/scan.py` | Legacy tiered scanner (free/pro/elite) |
| `modules/` | SENTINEL — 17 deep forensic modules |
| `degen_security_scanner/` | 7-phase Solana degen scanner |

Plus 7 capability engines (C1–C7) and 10 own-data engines, all backed by the
lite-chain-indexer ClickHouse store (`dex_events`, `evm_transfers`, `deployer_events`).

## 5-Minute Setup

```bash
# 1. Clone + install
git clone https://git.rugmunch.io/RugMunchMedia/rmi-backend.git
cd rmi-backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure env (create from example; defaults work for a local smoke test)
#    cp .env.example .env   # then set DATABASE_URL, CLICKHOUSE_URL if needed
cp .env.example .env

# 3. Run the API
uvicorn app.main:app --reload --port 8000
# Swagger: http://localhost:8000/docs
```

> **Prefer Docker?** `docker compose up -d` runs the full stack (API, ClickHouse,
> Redis). For a bare smoke test, only the API + ClickHouse are required.

Verify it's up:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

## First Scan

### Token scan (v2)

```bash
curl -X POST http://localhost:8000/api/v2/scanner/token/scan \
  -H "Content-Type: application/json" \
  -d '{"address": "<TOKEN_ADDRESS>", "chain": "solana"}'
```

Example response shape:

```json
{
  "status": "ok",
  "token": "0x...",
  "chain": "solana",
  "name": "ExampleToken",
  "symbol": "EXMP",
  "price_usd": 0.0001,
  "safety_score": 82,
  "confidence": 0.9,
  "risk_level": "low",
  "risk_flags": [],
  "green_flags": ["Liquidity locked", "Ownership renounced"],
  "modules_run": 24,
  "scan_duration_ms": 812,
  "tier": "free",
  "data_source": "databus",
  "fallback_used": false,
  "data_sources": {
    "own_data_modules": 18,
    "external_fallback_modules": 6,
    "total_modules": 24
  }
}
```

### Wallet scan (v2)

```bash
curl -X POST http://localhost:8000/api/v2/scanner/wallet/scan \
  -H "Content-Type: application/json" \
  -d '{"address": "<WALLET_ADDRESS>", "chain": "ethereum"}'
```

Returns labels, entity name/type, persona, `total_value_usd`, `token_count`,
`scam_tokens_held`, `scam_exposure_pct`, and a `risk_score`.

### Telegram

With the bot configured, run `/scan <address>` or `/wallet <address>` in the
RugMunch Telegram bot — same engines, results delivered as alerts.

## Architecture Overview

```
                    ┌────────────────────────────────────────────┐
                    │           lite-chain-indexer (v1.3.0)      │
                    │   EVM + Solana → ClickHouse                │
                    │   dex_events · evm_transfers ·             │
                    │   deployer_events · token_meta_ref         │
                    └───────────────┬────────────────────────────┘
                                    │ own-data (LOCAL tier wins)
                    ┌───────────────▼────────────────────────────┐
                    │                DataBus                      │
                    │  115 provider chains · 176 providers       │
                    │  LOCAL → FREE → PAID fallback chains       │
                    └───────────────┬────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌───────────────┐        ┌──────────────────┐        ┌──────────────────┐
│ UnifiedToken  │        │ Capability Engines│        │ Own-Data Engines │
│ Scanner       │        │ C1–C7 (wallet_flow,│       │ own_security,    │
│ (primary)     │        │ holder_clusters,  │        │ own_lp_lock,     │
│ + SENTINEL 17 │        │ attack_detector,  │        │ own_whale, wash, │
│ modules       │        │ cross_chain_entity│        │ holder_age, ...  │
└───────┬───────┘        └──────────────────┘        └──────────────────┘
        │
        ▼
┌───────────────────┐    ┌───────────────────┐    ┌───────────────────┐
│ HTTP API v2       │    │ Telegram bot      │    │ WebhookEngine     │
│ /scanner/token/scan│   │ /scan /wallet     │    │ 7 providers       │
│ /scanner/wallet/scan│  └───────────────────┘    └───────────────────┘
└───────────────────┘
```

Key flow: a scan request hits the API → UnifiedTokenScanner calls DataBus →
own-data ClickHouse engines answer first (`PREFER_OWN_DATA=true`) → missing
signals fall back through free APIs (GoPlus, DexScreener, CoinGecko…) → paid
providers last. Results are returned with per-module source attribution so you
always know whether a signal came from own data or a fallback.

## Where to Go Next

- **[SCANNER_STATUS.md](SCANNER_STATUS.md)** — capability matrix, engine inventory, quality metrics
- **[docs/scan-capabilities-spec.md](docs/scan-capabilities-spec.md)** — C1–C7/F0–F2 spec and build order
- **[docs/cross-chain-entity-tracking.md](docs/cross-chain-entity-tracking.md)** — C4 architecture (matching strategies, confidence scoring)
- **[docs/API_REFERENCE.md](docs/API_REFERENCE.md)** — full API reference
- **[docs/COMPETITIVE_LANDSCAPE.md](docs/COMPETITIVE_LANDSCAPE.md)** — how RugMunchScan compares to OSS scanners
- **Run tests:** `make test` (unit) · `make test-all` (all tests incl. integration)
