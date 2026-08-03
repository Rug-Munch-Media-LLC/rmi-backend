# RugBrain — Crypto-Native RAG Engine

**Status:** canonical · v1.0 · 2026-08-03
**Owner:** Rug Munch Media LLC Engineering
**Repo:** `rmi-backend` (`app/rag/`, `app/api/v1/rag/`, `app/routers/rag_v2.py`)
**Companion docs:** `docs/RAG_ARCHITECTURE.md` (deep dive, layers L1–L9),
`docs/RAG_STRATEGY.md` (v2.0 strategy)

---

## What It Is

RugBrain is an open-source retrieval-augmented generation engine purpose-built
for crypto intelligence. Part of the RugMunch Media OSS family.

It reads the entire threat surface — smart contracts, exploit post-mortems,
on-chain movements, scanner findings, and live news — and answers hard
questions with cited evidence instead of vibes.

## What Makes It Different

- **Crypto-native chunking**: Solidity-aware, whitepaper-aware, audit-aware
- **Own-data-first**: Queries our lite indexer (282M+ transfers) before external APIs
- **Hybrid search**: Dense (bge-m3) + sparse (BM25) + cross-encoder reranker
- **Agentic**: LangGraph-powered with real-time tool use (price, gas, scan, news)
- **Evaluated**: Ragas metrics tracked over time (faithfulness, precision, recall)
- **Observable**: Phoenix tracing on every RAG operation

## Quick Start

### Prerequisites

The stack is Docker-first (fleet standard):

| Service | Image / default | Role |
|---------|-----------------|------|
| Infinity | `infinity-rag:8001` | Embedding (`BAAI/bge-m3`, 1024-dim) + reranking (`BAAI/bge-reranker-v2-m3`) |
| Qdrant | `rmi-qdrant:6333` (`qdrant/qdrant:v1.12`) | Vector store — 18 collections, 4,354+ points |
| Redis | existing `rmi-redis` | Dedup hashes (`rag:hashes:*`), embed cache, rate limits |
| Postgres | existing `rmi-postgres` | Source of truth for envelopes (Qdrant is a projection) |
| Phoenix (Arize) | `phoenix:4317` / UI `phoenix:6006` | OpenInference tracing, project `rugmunch-rag` |

### Configuration

Set in the environment (or `app/config.py`):

```bash
export INFINITY_URL=http://infinity-rag:8001   # embedding + rerank
export QDRANT_URL=http://rmi-qdrant:6333       # vector store
export PHOENIX_ENABLED=true                     # tracing
export PHOENIX_ENDPOINT=http://phoenix:4317
export PHOENIX_PROJECT=rugmunch-rag
export LLAMA_RAG_ENABLED=false                  # legacy pipeline — superseded by RugBrain
```

### API

```bash
# Hybrid search + rerank (dense + BM25 RRF fusion, cross-encoder rerank)
curl -X POST http://localhost:8000/api/v1/rag/v2/search \
  -H "Content-Type: application/json" \
  -d '{"query": "was this contract audited?", "top_k": 10}'

# Ingest a document envelope (idempotent, content-hash dedup)
curl -X POST http://localhost:8000/api/v1/rag/v2/ingest \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/post-mortem", "content": "...", "content_type": "news"}'

# Agentic Q&A (LangGraph: route → retrieve → tool → synthesize → cite)
curl -X POST http://localhost:8000/api/v1/rag/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "which whale wallets accumulated in the last 48h?"}'

# RAG observability metrics
curl http://localhost:8000/api/v1/rag/metrics
```

Auth: unified auth (JWT) + x402 payment gate for premium endpoints (ADR-005).
`top_k ≤ 50`, query ≤ 2048 chars.

## Architecture

```
 PRODUCERS                                R U G B R A I N
 ┌────────────────────────────┐   ┌───────────────────────────────────────────┐
 │ RugMunchScan  findings     │──▶│ L1 Ingestion  Crawl4AI/pryscraper, GitHub, │
 │ lite indexer  on-chain     │──▶│               social, on-chain, scans      │
 │ pryscraper    web/news     │──▶│ L2 Chunking   content-type router          │
 │ GitHub        contracts    │──▶│               (code AST-aware, whitepaper  │
 └────────────────────────────┘   │                semantic, news fixed-size)   │
                                  │ L3 Embedding  Infinity bge-m3 (1024-dim)   │
                                  │ L4 Storage    Qdrant 18 collections        │
                                  │               Redis · Postgres · KG        │
                                  │ L5 Retrieval  QueryExpander → RRF →        │
                                  │               reranker → ContextBuilder    │
                                  │ L6 Orchestr.  LangGraph router + tool-use  │
                                  └──────────────┬────────────────────────────┘
                                                 ▼
                     L7 Eval (Ragas + golden set)   L8 Obs (Phoenix)
                     L9 API /api/v1/rag/v2/search · /ingest · /ask (+SSE)
```

### Collection taxonomy (18)

| Domain | Collections |
|--------|-------------|
| security | `threat_intel` · `scam_intel` · `exploit_db` · `contract_audit` · `phishing_db` · `wallet_labels` · `deployer_history` |
| code | `code_snippets` · `contract_source` · `audit_evidence` |
| market | `market_intel` · `whale_movements` · `narrative_signal` |
| news | `news_articles` · `social_signal` · `regulatory` · `research` |
| shared | `scanner` (findings) · `feedback` (human corrections) |

### Key invariants

1. **Ground everything** — every answer carries sources, scores, provenance.
2. **Fail-open retrieval, fail-closed generation** — no evidence → "no data".
3. **One embedding model** — bge-m3 (1024-dim) everywhere.
4. **Consume, don't rebuild** — reuse pryscraper / lite indexer / RugMunchScan
   as ingestion producers.
5. **Qdrant is a projection** — Postgres is the system of record; re-index from Postgres.

## Evaluation & Observability

- **Ragas**: faithfulness ≥ 0.85, answer relevance ≥ 0.80, context precision
  ≥ 0.75, context recall ≥ 0.85 on a crypto golden set (300+ Q&A pairs).
  Runs as a CI gate on every RugBrain change.
- **Phoenix (Arize)**: traces `query → retrieve → rerank → generate`; latency
  budgets p95 search < 500ms, p95 ask < 5s (first token < 1.5s); token usage
  and cost per request. Instrumentation is fail-open.

## References

- `docs/RAG_ARCHITECTURE.md` — layer-by-layer design, ADR summary, roadmap
- `docs/RAG_STRATEGY.md` — v2.0 strategy
- `docs/COMPETITIVE_LANDSCAPE.md` — product family & positioning
- `SCANNER_STATUS.md` — RugBrain RAG stack status
