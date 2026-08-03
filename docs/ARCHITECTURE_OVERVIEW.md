# RugMunch Platform — Architecture Overview

> **Status:** canonical · **Owner:** Rug Munch Media LLC Engineering · **Last updated:** 2026-08-03
>
> This is the **master architecture document** for the RugMunch platform. It shows how the three
> products — **Lite Chain Indexer** (data layer), **RugMunchScan** (security layer), and
> **RugBrain RAG** (intelligence layer) — fit together and share infrastructure. For depth on any
> subsystem, follow the references at the end of each section.

The three products form a pipeline: **Lite Chain Indexer** produces the raw on-chain data that
**RugMunchScan** analyzes and scores, and both feed **RugBrain RAG**, which lets users ask
natural-language questions about what the platform has indexed, scanned, and learned from the news.

---

## How the Products Fit Together

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                  PUBLIC CHAINS (14+ chains)               │
                        │   Ethereum Base BSC Polygon Arbitrum Optimism Avalanche   │
                        │   Fantom Linea Scroll zkSync Mantle Gnosis Celo + Solana  │
                        │   + Tron — via free public RPC pool (123 endpoints)      │
                        └──────────────────────────────┬───────────────────────────┘
                                                       │
                                                       ▼
        ┌──────────────────────────────────────────────────────────────────────────┐
        │  ① LITE CHAIN INDEXER  (v1.3.0 · data layer · /srv/work/repos/lite-     │
        │                       chain-indexer)                                     │
        │  asyncio daemon → ClickHouse `wallet_memory`                             │
        │  · evm_transfers      (285M)      · token_meta_ref / holder_balances     │
        │  · dex_events         (13M)       · parquet_export_state                 │
        │  · deployer_events    (920K)      · block_checkpoints / reconciliation   │
        │  · solana_spl_balance_changes (1.35M)                                    │
        └───────────────┬──────────────────────────────┬───────────────────────────┘
                        │                              │
           hot path:    │                     cold tier:│  hourly Parquet snapshots
           ClickHouse   ▼                              ▼  → B2 (Backblaze), DuckDB-queryable
        ┌────────────────────────────┐      ┌──────────────────────────┐
        │  ② RUGMUNCHSCAN            │      │  DuckDB / parquet        │
        │  (security layer ·         │      │  (historical analytics,   │
        │   rmi-backend app/domains/ │      │   cold queries)           │
        │   scanner)                 │      └──────────────────────────┘
        │  C1–C7 capability engines  │
        │  own-data-first reads      │
        │  + DataBus free API        │
        │  fallback (115 chains)     │
        │  → 0–100 explainable score │
        │  → Telegram / webhook out  │
        └──────────────┬─────────────┘
                       │ findings + alerts
                       ▼
        ┌──────────────────────────────────────────────────────────────────────────┐
        │  ③ RUGBRAIN RAG (intelligence layer · rmi-backend app/rag + rmi-rag)     │
        │  · scanner findings + Crawl4AI news → embed (Infinity bge-m3)            │
        │  · store in Qdrant (18 collections) · rerank (bge-reranker-v2-m3)        │
        │  · LangGraph agentic retrieval → answer with sources                      │
        │  · Ragas evaluation · Phoenix tracing                                    │
        └──────────────────────────────────────────────────────────────────────────┘
                       │
                       ▼
        ┌─────────────────────────────┐   ┌──────────────────────────────┐
        │  Consumers                   │   │  Shared infrastructure       │
        │  · rugmunch.io frontend       │   │  Infinity (8001) · Qdrant    │
        │  · Telegram bot               │   │  (6333) · Redis (6379)       │
        │  · MCP / CLI / SDK / x402 API│   │  Postgres (5432) · Neo4j     │
        └─────────────────────────────┘   │  (7474/7687) · rmi_network    │
                                          └──────────────────────────────┘
```

Data flows one direction (indexer → scanner → RAG), while every product shares the same
`rmi_network` bridge and the cross-cutting services listed at the bottom.

---

## Lite Chain Indexer → Data Layer

**Repo:** `/srv/work/repos/lite-chain-indexer` · **Version:** v1.3.0 · **License:** MIT (open source)

The indexer is the platform's ground-truth data source: a single `asyncio`-parallel Python daemon
that watches transfer, DEX swap, and deployer events across **14+ chains (14 EVM + Solana + Tron)**
using only **free public RPC endpoints** — no node, no gateway, no paid API. It is demand-aware:
long-tail chains only index DEX/deployer event types when a scanner query actually requests them
(file-touch signaling, persisted in `state`).

### What it indexes

| Event type | ClickHouse table | Production rows (2026-08-03) |
|---|---|---|
| ERC-20 / TRC-20 transfers | `wallet_memory.evm_transfers` | **285M** |
| DEX swap events (Uniswap V2/V3 + forks) | `wallet_memory.dex_events` | **13M** |
| Pair / pool deployments | `wallet_memory.deployer_events` | **920K** |
| Solana SPL balance changes | `wallet_memory.solana_spl_balance_changes` | **1.35M** |

> `[REVIEW]` The README snapshot (2026-08-01) reports 278M+ transfers / 58M+ DEX events; the
> numbers above are the current production ClickHouse counts and should be re-verified against
> `wallet_memory` before public release. Reconcile the DEX-event delta (58M vs 13M) — it may reflect
> table cleanup or a broader definition.

### How data is stored (ClickHouse tables)

The `wallet_memory` database (ClickHouse `24-alpine`) holds the live tables:

| Table | Purpose |
|---|---|
| `evm_transfers` | Transfer events for EVM ERC-20 + Tron TRC-20 |
| `dex_events` | DEX swap events with pool/route detail |
| `deployer_events` | Pair/pool deployment events (universal deployer detection) |
| `solana_spl_balance_changes` | Solana SPL balance deltas |
| `token_meta_ref` | Token symbol/metadata reference |
| `holder_balances` | Maintained balance view for holder queries |
| `parquet_export_state` | Cold-tier export checkpoints |
| `block_checkpoints` | Per-chain reorg-aware checkpoints |
| `reconciliation_results` | Data-integrity reconciliation output |

Reorgs are handled via `block_checkpoints` hash verification; reconciliation samples random
wallets from `evm_transfers` to confirm consistency.

### How the scanner reads from it

RugMunchScan's capability engines query ClickHouse directly — this is the **own-data-first**
principle. Engine → data mapping:

| Engine | ClickHouse tables consumed |
|---|---|
| C1 wallet_flow | `evm_transfers` + `wallet_labels` |
| C2 holder_clusters | `evm_transfers` + `dex_events` + `wallet_labels` |
| C3 new_token_radar | `deployer_events` + C7 fingerprint |
| C4 cross_chain_entity | `deployer_events` + `evm_transfers` (same-deployer / same-funder links) |
| C5 hype_score | `dex_events` + `evm_transfers` + trending |
| C6 attack_detector | `dex_events` (sandwich / pump-dump / flash-loan) |
| C7 deployer_fingerprint | `deployer_events` + `dex_events` + `evm_transfers` + `wallet_labels` |

### Cold tier (hourly Parquet → B2)

- ClickHouse serves the **hot path** (near-chain-tip freshness, ~30s from tip on free RPCs).
- The indexer exports **Parquet snapshots to B2** on a **6-hourly** schedule on the fleet
  (daily in the in-repo script), recording checkpoints in `parquet_export_state`.
- Historical snapshots are queryable with **DuckDB** — no ClickHouse required for cold analytics,
  no vendor lock-in. `rmi-rag` additionally exposes a DuckDB query endpoint
  (`/api/v1/rag/query`) over the B2 permanence snapshots.

---

## RugMunchScan → Security Layer

**Location:** `rmi-backend/app/domains/scanner` · **Status doc:** `SCANNER_STATUS.md` ·
**Capabilities spec:** `docs/scan-capabilities-spec.md` · **Scoring:** `docs/RISK_SCORING.md`

RugMunchScan is the security layer: it turns indexer data (plus free API enrichment) into
token/wallet risk assessments with **explainable scores**. It is the primary engine behind the
scanner API, the Telegram bot, and webhook alerts.

### Scanner engines

- **4 core scanner engines** — token scan, wallet scan, and related risk pipelines.
- **7 capability engines (C1–C7)** — all in `app/domains/scanner/engines/`:
  - **C1 — Wallet-to-Wallet Flow Visualization** (`engines/wallet_flow.py`)
  - **C2 — BubbleMaps-style Holder Clustering** (`engines/holder_clusters.py`)
  - **C3 — New Token Discovery + Early Warning** (`engines/new_token_radar.py`)
  - **C4 — Cross-Chain Entity Resolution** (`engines/cross_chain_entity.py` — v1 complete: same-deployer
    + same-funder links with confidence scoring + evidence)
  - **C5 — Hype Score** (`engines/hype_score.py`)
  - **C6 — Time-Based Attack Detection** (`engines/attack_detector.py` — sandwich / pump-dump /
    flash-loan; pure own-data)
  - **C7 — Deployer Behavioral Fingerprint** (`engines/deployer_fingerprint.py`) — the spine for
    early-warning triage (C3)
- **10 own-data engines** plus `UnifiedTokenScanner` (`unified/scanner.py`) — the primary
  DataBus-powered engine with `PREFER_OWN_DATA=true`.

### How it uses indexer data (own-data-first)

The scanner reads ClickHouse `wallet_memory` tables directly (see the mapping table in the
previous section). `PREFER_OWN_DATA=true` means the LOCAL (own-data) tier always wins when a
provider can answer a query; external APIs are **enrichment only** — the pipeline never depends
on a paid API for a core answer.

### Free API fallback chains

DataBus aggregates **176 providers** across categories (token, wallet, intel, social, system,
on-chain, security); **115 provider chains** are live (`build_provider_chains()`, 2026-08-03).
When own data is unavailable, the scanner falls back through the free tier:
GoPlus (token security) → DexScreener (pairs/liquidity) → CoinGecko (price/market) → Birdeye
(Solana) → Etherscan/Blockscout (EVM) → Helius (Solana RPC), respecting per-provider rate limits.

### Scoring methodology

- **Unified engine (canonical):** `unified/scoring.py` — 0–100 **safety score**
  (higher = safer); every token starts at a neutral baseline of 50; each signal adds a flat
  penalty or bonus.
- Scores are **explainable by construction**: every finding carries a breakdown so any score can
  be traced to specific signals (see `docs/RISK_SCORING.md`).
- Legacy tiered (`service/scoring.py`) and SENTINEL composite
  (`app/domains/scanners/sentinel_pipeline/risk.py`) implementations still run for parity
  (public scanner API → legacy; SENTINEL → composite risk).

### Telegram / webhook output

Scan results and early-warning alerts are pushed out via `app/domains/telegram/alert_hooks.py`
(`send_scanner_alert`, `should_send_scanner_alert`) — the same hooks power the Telegram bot and
generic webhook delivery from `service/scan.py`.

**Test coverage:** 535 scanner tests (live `pytest` collection, 2026-08-03); 3,690 tests across
the full suite.

---

## RugBrain RAG → Intelligence Layer

**Location:** `rmi-backend/app/rag/` + `/srv/work/repos/rmi-rag/` ·
**Architecture:** `docs/RAG_ARCHITECTURE.md` · **Strategy:** `docs/RAG_STRATEGY.md` ·
**Product brief:** `docs/RUGBRAIN.md`

RugBrain is the natural-language interface over everything the platform knows: scanner findings,
crawled news, threat-intel feeds, and indexed on-chain context. API at `rmi-rag-api:8012`
(rag.rugmunch.io), RAG v2 wired behind `app/routers/rag_v2.py`.

### How it ingests scanner findings + crawled news

| Source | Path |
|---|---|
| Scanner findings | `rmi-rag/scripts/ingest_threats.py` + scanner subscriber/poller (`scanner_subscriber`) |
| News | Crawl4AI (`app/rag/crawler.py` — JS-aware crawling, lazy `crawl4ai` import) + `rmi-rag/scripts/news_to_rag.py` / `rmi-rag/scripts/rmi-news-ingest.sh`; 3-layer extraction (trafilatura → FlareSolverr → Chromium) |
| Threat intel | `rmi-rag/scripts/ingest_rekt_news.py`, `rmi-rag/scripts/ingest_defihacklabs.py`, `rmi-rag/scripts/ingest_swc_registry.py`, `rmi-rag/src/rmi_rag/seed_scams.py` |
| B2 permanence | DuckDB query endpoint over Parquet snapshots |

Postgres is the **system of record** for envelopes; Qdrant is a **projection** — the RAG pipeline
re-indexes from Postgres without loss.

### Embedding + reranking pipeline

- **Embedding:** Infinity server (`infinity-rag:8001`) runs **BAAI/bge-m3** (1024-dim, ONNX /
  `optimum` engine).
- **Reranking:** same server runs **BAAI/bge-reranker-v2-m3** (`torch` engine — no ONNX export
  exists).
- **Search:** hybrid — dense (bge-m3) + sparse (BM25) + cross-encoder rerank, with a single
  embedding model everywhere (1024-dim) to keep the vector space consistent.

### LangGraph agentic retrieval

- **RugBrain RAG v2** (`app/rag/graph.py`) builds a LangGraph `StateGraph` research orchestrator
  (retrieve → cross-reference → synthesize); the compiled graph is cached at module level and
  served by `app/routers/rag_v2.py` (`/api/v1/rag/*`).
- `app/rag/tracing.py` provides the custom-httpx span helper the graph uses for observability
  (no auto-instrumentable LangChain/OpenAI call path).

### Evaluation + observability

- **Ragas evaluation:** `rmi-rag/src/rmi_rag/evaluation.py` + `rmi-rag/src/rmi_rag/eval_ragas.py`,
  exposed at `GET /api/v1/eval/run` with golden-set metrics — **deployed 2026-08-01** on :8012.
- **Phoenix tracing:** `app/rag/tracing.py` `setup_phoenix()` registers the OTLP exporter
  (`arize-phoenix-otel`, `phoenix.otel.register`) to **http://phoenix:4317**; Phoenix UI at
  **http://phoenix:6006**. Traces span query → retrieve → rerank → generate with latency budgets
  (p95 search < 500ms, p95 ask < 5s, first token < 1.5s) and token-usage accounting.

**Vector store:** Qdrant — **18 collections**, **4,380 points** (2026-08-03; `docs/RUGBRAIN.md`
snapshot says 4,354+ — `[REVIEW]` reconcile).

---

## Cross-Cutting Concerns

All products share `rmi_network` and these services:

| Service | Container / endpoint | Role |
|---|---|---|
| **Infinity** | `infinity-rag:8001` | Shared embedding (bge-m3) + reranking (bge-reranker-v2-m3) engine. One instance serves both RugBrain and any RAG path in the backend. |
| **Qdrant** | `rmi-qdrant:6333` (v1.12) | Shared vector store — 18 RugBrain collections. |
| **Redis** | `rmi-redis:6379` (redis:7-alpine) | Cache (L1 RAM → L2 Redis, `maxmemory 512mb`, allkeys-lru) + message bus for worker/job coordination. |
| **Neo4j** | `rmi-neo4j:7474/7687` (5-community, APOC) | Entity knowledge graph — wallet clustering, entity resolution, cross-chain actor linkage. |
| **PostgreSQL** | `rmi-postgres:5432` (16-alpine) | Application state + system of record for RAG envelopes (Qdrant is a projection). |
| **ClickHouse** | `rmi-clickhouse:8123/9000` (24-alpine) | Analytics + indexer hot store (`wallet_memory`). |
| **MinIO / Redpanda** | `rmi-minio:9000/9001` · `rmi-redpanda` | Object storage + event streaming (opt-in `full` profile). |

Data-shape references: `docs/SCHEMA.md`, `docs/DATABASE.md`.

---

## Deployment

**Compose layout:** the platform is split across three compose files, all attached to the
`rmi_network` bridge (`172.28.0.0/16`):

| Compose file | Provides |
|---|---|
| `rmi-backend/docker-compose.yml` | Databases, Ollama, MinIO, Redpanda, app (backend + worker), monitoring (`full` profile) |
| `fleet-infra/rmi-infra/docker-compose.infrastructure.yml` | Infinity (`infinity-rag`), Qdrant, Prometheus, Grafana |
| `rmi-rag/docker-compose.yml` | RAG API (`rmi-rag-api:8012`) + one-shot ingest |

### Service matrix

| Container | Image | Port(s) | Resources | Profile |
|---|---|---|---|---|
| `rmi-backend` | multistage build | 127.0.0.1:8000 | read-only rootfs, no caps | default |
| `rmi-worker` | worker build | — | read-only rootfs | `full` |
| `rmi-rag-api` | rmi-rag build | 127.0.0.1:8012 | 1 CPU / 1 GB | default |
| `infinity-rag` | michaelf34/infinity:latest-cpu | 127.0.0.1:8001 | 24 GB / 16 CPUs, `cpuset "0-15"` | default (infra) |
| `rmi-qdrant` | qdrant/qdrant:v1.12 | 6333 | 1 GB | `full` |
| `rmi-redis` | redis:7-alpine | 127.0.0.1:6379 | 512 MB maxmemory | default |
| `rmi-postgres` | postgres:16-alpine | 127.0.0.1:5432 | — | default |
| `rmi-clickhouse` | clickhouse/clickhouse-server:24-alpine | 8123, 9000 | — | default |
| `rmi-neo4j` | neo4j:5-community | 7474, 7687 | 2 GB heap | `full` |
| `rmi-ollama` | ollama/ollama:latest | 11434 | 8 GB / 4 CPUs | default |
| `rmi-minio` | minio/minio:latest | 9000, 9001 | — | `full` |
| `rmi-redpanda` | redpandadata/redpanda:latest | — | 1 GB | `full` |
| `rmi-prometheus` / `rmi-grafana` | prom/prometheus · grafana/grafana | 9090 · 3000 | 1 GB each | `full` |
| `phoenix` (Arize) | arize-phoenix | 6006 (UI), 4317 (OTLP) | — | infra (referenced by `app/rag/tracing.py`) |

### CPU allocation

- **Infinity** — the embedding/reranking engine is the CPU-heavy service (ONNX bge-m3
  optimization phase spikes ~12.5 GB transiently; steady state ~5 GB, capped at 24 GB).
- **Indexer + scanner** — run as backend processes on the same host and should be pinned away
  from Infinity's cores.
- **Intended split:** Infinity on **cores 0–11**, indexer/scanner on **cores 12–15**.
  `[REVIEW]` The deployed `fleet-infra` compose currently sets `cpuset: "0-15"` for
  `infinity-rag` (16 CPUs). Apply the 0–11 / 12–15 split if the host is shared with the indexer
  and scanner, and update this table after the change.

---

## Related Documents

| Document | Covers |
|---|---|
| `docs/ARCHITECTURE.md` | Full system architecture (API, DataBus, cache, risk fusion) |
| `docs/RAG_ARCHITECTURE.md` | RugBrain deep dive: layers, decisions, observability plan |
| `docs/RAG_STRATEGY.md` | RAG strategy and roadmap |
| `docs/RUGBRAIN.md` | RugBrain product brief (collections, models, auth) |
| `docs/RISK_SCORING.md` | Scoring methodology and breakdowns |
| `docs/scan-capabilities-spec.md` | C1–C7 capability specs |
| `SCANNER_STATUS.md` | Live scanner engine status |
| `docs/SCHEMA.md` · `docs/DATABASE.md` | Data shapes and tables |
| `docs/DEPLOYMENT.md` | Deployment runbooks |
| `/srv/work/repos/lite-chain-indexer/README.md` | Indexer usage, chains, RPC pool |
| `/srv/work/repos/rmi-rag/ARCHITECTURE-V2.md` | RAG v2 refactor state |
