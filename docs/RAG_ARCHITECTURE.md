# RugBrain — Crypto-Native RAG Architecture

**Status:** canonical · v1.0 · 2026-08-03
**Owner:** Rug Munch Media LLC Engineering
**Repo:** `rmi-backend` (`app/rag/`, `app/api/v1/rag/`) — OSS sibling to lite-chain-indexer, RugMunchScan, pryscraper
**Companion docs:** `docs/RAG_STRATEGY.md` (v2.0), `RAG_MODERNIZATION.md` (2026 standards)

---

## 1. Product

### 1.1 Name & Elevator Pitch

# **RugBrain**

> *RugBrain is the crypto-native retrieval engine that reads the entire threat
> surface — smart contracts, exploit post-mortems, on-chain movements, and live
> news — and answers hard questions with cited evidence instead of vibes.*

RugBrain is a production-grade RAG system specialized across four domains:

| Domain | What it answers | Sources |
|--------|----------------|---------|
| **Security** | "Was this contract audited? Which reentrancy patterns does it match? How was the Euler exploit executed?" | Smart contract audits, vulnerability patterns, exploit analyses, scanner findings |
| **Programming** | "Show me a safe Solidity withdrawal pattern. How do you guard a Move module against reentrancy?" | GitHub code repos, Solidity/Rust/Move docs, audit code snippets |
| **Market Intel** | "Which whale wallets accumulated in the last 48h? What narrative is driving this cycle?" | On-chain analytics, whale movements, narrative detection |
| **News** | "Summarize today's regulatory moves with sources." | Real-time news aggregation, RSS, social signals |

### 1.2 Why "RugBrain" (name rationale)

1. **Brand family consistency.** Every RugMunch sibling uses the `Rug` prefix:
   RugMunchScan, RugCharts, RugMaps, RugProof, rugmunchbot. `RugBrain` slots
   into the family with zero naming friction and reinforces the parent brand.
2. **"Rug" is the security DNA.** The product's #1 pillar is rug-pull/exploit
   detection. The word that outsiders read as a negative is the brand's
   core value proposition — we own it deliberately, same as RugProof does.
3. **"Brain" > "RAG".** RAG is an implementation detail that will evolve
   (graph RAG, agentic retrieval, tool-use). "Brain" communicates the
   intelligence layer without baking in a 2025-era acronym.
4. **Distinctive & searchable for OSS.** Near-zero collision on GitHub
   (unlike `ChainMind`, `TokenSage`, `BlockLens`, `CryptoVault` which all
   collide with existing products).
5. **Defensible promise.** `AlphaRAG` overpromises trading returns (compliance
   risk). `RugBrain` promises intelligence + security — something we can
   actually deliver and measure.

**Runner-up:** `ChainMind` (clean, chain-native) — rejected on collision risk
and weaker brand tie. **Rejected:** `MunchRAG` (acronym in name ages poorly),
`DeFiBrain` (too narrow — product covers security, code, news, and market
intel), `AlphaRAG` (financial-advice overpromise), `CryptoVault` (sounds like
a wallet), `BlockLens` (generic).

### 1.3 Guiding Principles

1. **Ground everything.** Every answer must carry sources, scores, and
   provenance. No unsupported claims in Security domain — period.
2. **Fail-open retrieval, fail-closed generation.** If the reranker or vector
   store hiccups, search degrades gracefully (existing behavior). If
   grounding is missing, the LLM must say "I don't have evidence" instead of
   hallucinating.
3. **Hybrid by default.** Dense + sparse + reranker. Never ship a single-
   vector search as the final answer.
4. **Consume, don't rebuild.** Reuse pryscraper, the lite chain indexer, and
   RugMunchScan as ingestion producers. RugBrain is the retrieval layer, not
   a second crawler.
5. **One embedding model.** bge-m3 (1024-dim) everywhere. The 3-embedder
   inconsistency documented in RAG_MODERNIZATION.md is a bug, not a feature.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              R U G B R A I N                                  │
│                       Crypto-Native Retrieval-Augmented Generation            │
└─────────────────────────────────────────────────────────────────────────────┘

 PRODUCERS (existing siblings)              L1 INGESTION
 ┌──────────────────────────────┐    ┌───────────────────────────────────────┐
 │ RugMunchScan  findings       │───▶│ Crawl4AI / pryscraper  → web pages    │
 │ lite indexer   on-chain      │───▶│ GitHub API             → contracts/code│
 │ pryscraper     scraped docs  │───▶│ Twitter/X + Discord    → social signal │
 │ rmi-backend    scanner DB    │───▶│ lite indexer webhooks  → on-chain events│
 │ news/RSS feeds               │───▶│ RugMunchScan webhook   → scan results  │
 └──────────────────────────────┘    └──────────────┬────────────────────────┘
                                                    │
                              L2 PROCESSING         ▼
                          ┌─────────────────────────────────────────┐
                          │ content-type router → chunkers          │
                          │  code: AST-aware │ whitepaper: semantic │
                          │  news: fixed-size │ social: short       │
                          │  dedup: SHA-256 (Redis) · quality gate  │
                          └───────────────────┬─────────────────────┘
                                              ▼
                          L3 EMBEDDING   ┌──────────────────────────────┐
                          (Infinity)     │ bge-m3 dense 1024-dim        │
                                         │ + sparse (BM25/SPLADE)        │
                                         └──────────────┬───────────────┘
                                              ▼
                          L4 STORAGE     ┌──────────────────────────────┐
                                         │ Qdrant (vector + sparse +    │
                                         │  payload filters)            │
                                         │ Redis (dedup, embed cache,   │
                                         │  rate limit)                 │
                                         └──────────────┬───────────────┘
                                              ▼
                          L5 RETRIEVAL   ┌──────────────────────────────┐
                                         │ QueryExpander                │
                                         │ dense+sparse → RRF fusion    │
                                         │ RelevanceScorer              │
                                         │ bge-reranker-v2-m3 (2-stage) │
                                         │ ContextBuilder (citations)   │
                                         └──────────────┬───────────────┘
                                              ▼
                          L6 ORCHESTRATION ┌─────────────────────────────┐
                                          │ Router: real-time vs history │
                                          │ LangGraph agentic research   │
                                          │ Graph-RAG fusion (KG)        │
                                          │ tool-use (indexer RPC, scan) │
                                          └──────────────┬───────────────┘
                                              ▼
        L9 API ────────▶ /api/v1/rag/v2/search · /ingest · /ask (+SSE stream)

        L7 EVAL (Ragas + golden set)   ·   L8 OBS (Phoenix/OpenInference)
```

---

## 3. Layer-by-Layer Design

### Layer 1 — Ingestion

**Status:** partially built (`app/rag/ingestion.py`, `processor.py`,
`ingestion_models.py`, 5-min scheduler, 200 docs/min rate limit).

| Producer | Mechanism | Target collection(s) |
|----------|-----------|----------------------|
| **Web** | Crawl4AI via pryscraper (3-layer free stack: trafilatura → FlareSolverr → Chromium) | `research`, `news` |
| **GitHub** | GitHub API (code search by language/topic), webhook-driven on starred/audited repos | `code`, `contract_audit` |
| **Social** | X/Twitter API + Discord archive crawler (pryscraper) | `social_signal` |
| **On-chain** | lite-chain-indexer webhook/CDC → normalized event envelope | `onchain` |
| **Scans** | RugMunchScan findings (existing `FeedbackRecord` path, extend to full doc) | `scanner`, `scam_intel` |
| **Curated** | Rekt DB, Chainabuse, DeFi exploits (gap identified in RAG_MODERNIZATION.md) | `exploit_db` |

**Design notes**
- **Envelope normalization.** Every producer emits the same `DocumentEnvelope`:
  `{id, source, content_type, domain, url, timestamp, chain, addresses[],
  author, raw_metadata}`. One schema upstream → one chunker/embedder path
  downstream.
- **Idempotent ingestion.** `doc_id = sha256(canonical_url | content)` so
  re-scrapes are no-ops. Dedup at content-hash level (Redis
  `rag:hashes:{collection}` — already implemented in `chunking.py`).
- **Provenance is payload.** Every Qdrant point carries `source_url`,
  `published_at`, `producer` (which sibling emitted it), `domain`. Citations
  in answers come from this, not from LLM memory.
- **Backpressure.** Existing 200/min ingest cap + batch size 50 stay. Add
  per-producer priority: scanner findings > on-chain events > news > social.

### Layer 2 — Processing & Chunking

**Status:** built (`app/rag/chunking.py` — recursive char splitter, 512 chars,
50 overlap, 32 min chars, quality score, SHA-256 dedup).

**Target: content-type router → per-type chunker.** One base pipeline,
specialized by `content_type`:

| Content type | Chunker | Chunk target | Why |
|--------------|---------|--------------|-----|
| **Code** | AST-aware (tree-sitter per language: Solidity, Rust, Move) | function/contract/module boundary | A vulnerability lives in a function, not across 500 random lines. Split on `contract`/`function`/`module` nodes; attach symbol path as metadata. |
| **Whitepaper / audit report** | Semantic (markdown heading + paragraph boundaries) | ~512 tokens | Preserve document structure; headings become retrieval anchors. |
| **News** | Fixed-size sliding window | 512 tokens, 15% overlap | Headlines + lede should survive in one chunk; no structure to honor. |
| **Social** | Whole-message (short) | 1 post = 1 chunk | Context is the thread; never split a tweet mid-thread. |
| **Scanner findings** | Structured (JSON → labeled fields) | per-finding | Risk flags, scores, and token address must co-locate in one point. |

**Token vs char units.** Current code chunks at 512 *characters* (~128–200
tokens) — conservative but safe. Target: 512 *tokens* (bge-m3 supports
8,192, but 512 keeps retrieval precision high and matches the 2026-standard
stated in RAG_MODERNIZATION.md). Implement token-aware splitting using the
tokenizer (tiktoken/bge tokenizer or Infinity's token count) with ~15%
overlap (~75 tokens). Keep the existing char fallback for non-tokenized
content.

**Quality gate (exists, extend).** Current `_quality` scores length vs
special-char ratio. Add: spam-repetition ratio (already in config, 0.3
threshold), language detection, and domain-specific rejection (e.g. code
chunks must contain at least one symbol; news chunks must contain ≥1 named
entity).

### Layer 3 — Embedding

**Status:** built (`app/rag/embedder.py` CryptoEmbedder, `embeddings.py`),
Infinity configured (`infinity_url = http://infinity-rag:8001`).

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Dense model** | `BAAI/bge-m3` via Infinity | 1024-dim, 8K context, strong multilingual + crypto jargon tolerance, free self-hosted |
| **Sparse model** | BM25 sparse vectors (Qdrant native sparse) or SPLADE-v3 via Infinity | Keyword precision for addresses, contract names, CVE IDs, chain names that dense vectors blur |
| **Reranker** | `BAAI/bge-reranker-v2-m3` (already wired in `rerank.py`, fail-open) | Cross-encoder, final relevance gate before LLM |

**Design notes**
- **One dense model, enforced.** Migrate legacy 768-dim (nomic) and 384-dim
  (bge-small) collections to 1024-dim bge-m3. A dimension mismatch across
  collections silently breaks hybrid retrieval. Embedding provider enum in
  `models.py` stays, but production default = `infinity`.
- **Hybrid vector layout in Qdrant.** Each point carries `dense` (1024) +
  `sparse` vectors in one point → one round-trip for both, fused with RRF
  (k=60 already in config).
- **Embedding cache.** Redis: `embed:{hash(query)} → vector` for repeated
  queries; `embed:{hash(chunk)} → vector` so re-ingestion of an unchanged
  chunk never re-embeds. TTL-aware eviction.
- **Batching.** Existing 50-doc batches; add per-batch retry with exponential
  backoff on Infinity 5xx (no retry storm during model reloads).

### Layer 4 — Storage

**Status:** Qdrant wired (engine.py, catalog, news ingest); Redis wired
(dedup). Target taxonomy: **18 collections across 4 domains** (user-stated
current Qdrant state; `threat_intel` holds 1,045+ points today).

```
DOMAIN        COLLECTIONS
security      threat_intel · scam_intel · exploit_db · contract_audit
              phishing_db · wallet_labels · deployer_history
code          code_snippets · contract_source · audit_evidence
market        market_intel · whale_movements · narrative_signal
news          news_articles · social_signal · regulatory · research
shared        scanner (findings) · feedback (human corrections)
```

| Store | Role | Design |
|-------|------|--------|
| **Qdrant** | Primary vector store | Dense + sparse per point; payload filters (chain, domain, time range, producer); snapshot-based backups to B2 (existing 3-tier backup: local → B2 → HF) |
| **Redis** | Support store | `rag:hashes:*` dedup sets, embedding cache, ingest rate-limit counters, query cache (short TTL for hot news queries) |
| **Postgres** (existing) | Source of truth for envelopes & metadata | Qdrant is a projection, not the system of record. Re-index = rebuild from Postgres. |
| **Knowledge Graph** (Redis, 5,614 nodes) | Graph-RAG augmentation | Addresses, entities, communities → narrative + cluster expansion at retrieval time (`app/graph_rag.py` exists) |

**Design notes**
- **Collections are logical, not physical.** One Qdrant collection per
  domain (4) with payload filters for collection-level semantics, OR keep
  per-collection physical indexes (18) for isolation. Decision (P0): keep
  physical per-domain collections, alias logical names — cheapest migration
  path, matches existing `RAG_COLLECTIONS` config.
- **TTL policy.** News/social: 90-day retention. Security/code: permanent.
  Enforced at ingest time (payload `expires_at`) + nightly purge job.
- **Embedding model tag on collection metadata.** Store `model: bge-m3-v1`,
  `dim: 1024` in collection config. Guards against silent model swaps.

### Layer 5 — Retrieval

**Status:** built and solid (`app/rag/retrieval.py` — `QueryExpander`,
`HybridRetriever`, `RelevanceScorer`, `ContextBuilder`; `rerank.py`).

```
query ─▶ QueryExpander ─▶ dense search (Qdrant) + sparse search (BM25)
                            │                    │
                            └────── RRF fusion (k=60) ──────┘
                                              │
                                        RelevanceScorer (alpha=0.7)
                                              │
                                 bge-reranker-v2-m3 cross-encoder  ← two-stage
                                              │
                                   ContextBuilder → cited, token-budgeted prompt
```

**Design notes**
- **Two-stage retrieval, explicit.** Stage 1: retrieve `top_k × 4` candidates
  via RRF fusion (recall). Stage 2: cross-encoder rerank to `top_k`
  (precision). Existing `HybridRetriever` already does this — keep it as the
  core, don't rewrite behind an abstraction layer.
- **Collection routing.** `retrieve_multi` exists; add domain-aware routing
  table: Security questions → `threat_intel` + `exploit_db` + `contract_audit`;
  code questions → `code_snippets`; news questions → `news_articles` +
  `social_signal` (recency-boosted).
- **Recency boost.** News/market queries get time-decay scoring on
  `published_at` (`app/temporal_decay.py` exists — wire it into scoring).
- **Filters.** Chain, domain, date-range, producer already supported by
  payload filters — surface them in the API.
- **Query expansion.** Existing synonym map (rug→rugpull/exit_scam, etc.) is
  the foundation; add per-domain expansions and LLM-based query rewriting in
  P1 (one LLM call to generate 2-3 query variants, RRF-fused — do NOT
  multiply vector-store round-trips for every query).

### Layer 6 — Orchestration

**Decision: LangGraph for the agentic layer; keep native clients for the
retrieval core.** Rejected: LlamaIndex as a framework (its abstractions would
wrap existing custom HybridRetriever/Qdrant code for no gain; LlamaIndex is
valuable as a reference for data-framework patterns, not as a dependency).

```
                          ┌──────────────────────────────────────────┐
   /ask ─────────────────▶│ Router (state machine)                    │
                          │  · real-time/on-chain → indexer RPC/tool  │
                          │  · historical/corpus   → vector retrieval │
                          │  · agentic multi-step  → LangGraph graph  │
                          └──────┬──────────────┬─────────────────────┘
                                 ▼              ▼
                       Vector retrieval    Tool-use (function calling)
                       (Layer 5)           · lite-indexer RPC (balance,
                                           ·  tx history, event logs)
                                           · RugMunchScan API (rescan)
                                           · news/RSS fetch
                                 ▼              ▼
                          ┌──────────────────────────────────────────┐
                          │ Generation: grounded synthesis + cites   │
                          │ (fail-closed: no evidence → "no data")   │
                          └──────────────────────────────────────────┘
```

- **Router is a state machine, not a prompt.** Rules first: on-chain address
  in query → hit the indexer tool; explicit "recent" news → live fetch;
  otherwise → vector retrieval. LLM only decides when rules are ambiguous.
- **LangGraph for multi-step research only** ("trace this exploit end-to-end",
  "build a dossier on this wallet"). Graph nodes: plan → retrieve → tool →
  synthesize → verify (check citations exist in context) → answer.
  Checkpointing gives retries + streaming + human-in-the-loop for free.
- **Graph-RAG fusion (P1).** On entity-bearing queries, expand via knowledge
  graph: cluster narratives from `graph_rag.py`, add community context to the
  retrieval set. Answers gain "who's connected to whom" instead of isolated
  chunks.
- **Guardrails.** Agent loop max-iteration cap (5), token budget per step,
  tool-call audit log, and a hard "no address enrichment for sanctioned
  entities" filter fed by existing OFAC/risk data.

### Layer 7 — Evaluation

**Status:** not active (Ragas mentioned in RAG_MODERNIZATION.md as a gap;
`rmi-rag-wt-ragas` sibling repo exists as a test harness).

| Metric | Tool | Target |
|--------|------|--------|
| Faithfulness | Ragas | ≥ 0.85 on golden set |
| Answer relevance | Ragas | ≥ 0.80 |
| Context precision | Ragas | ≥ 0.75 |
| Context recall | Ragas | ≥ 0.85 |
| Reranker lift | A/B on held-out set | +≥5 pts precision @5 vs dense-only |

- **Golden test set** — crypto-specific Q&A pairs, 300+ curated, covering all
  four domains, with the ground-truth context chunks attached. Categories:
  security exploits (with expected CVE/address mentions), code patterns
  (expected symbol names), whale/market (expected wallet labels), news
  (expected sources + dates). Versioned in-repo fixtures, not a database.
- **CI hook.** Eval runs on every RAG change (pre-merge), same gate style as
  the covgate/mutation gates in this repo's CI. Regressions block merge.
- **Online signals.** Feedback loop exists (`app/rag/feedback.py`,
  `FeedbackRecord`) — pipe thumbs-up/down into eval set curation monthly.

### Layer 8 — Observability

**Status: NOT WIRED — greenfield.** Note: the only "Phoenix" reference in
the app today is the Supabase Realtime *Phoenix channels* protocol
(`app/supabase_realtime.py`) — unrelated to Arize Phoenix. Plan below is new
work, not an extension.

| Concern | Tool | Instrumentation |
|---------|------|-----------------|
| Traces (query → retrieve → rerank → generate) | Arize Phoenix via OpenInference | Auto-instrument FastAPI + LangGraph callbacks; manual spans around HybridRetriever, rerank, Qdrant calls |
| Latency budgets | Phoenix spans + metrics | p95 search < 500ms, p95 ask < 5s (streaming first token < 1.5s) |
| Token usage | Phoenix token counting | Per-query embedding + LLM token totals; cost per request |
| Quality drift | Phoenix evaluations | Periodic Ragas scores posted as span evals |
| Alerts | Existing n8n + health route | Latency/token budget breach → n8n webhook (same pattern as backup-monitor) |

Run Phoenix as a Docker service (fleet standard — Docker-first, no local
process), data on the existing volume/backup plan. Instrumentation is
additive: `app/rag/` modules get decorator-based spans, fail-open like the
reranker (observability must never take search down).

### Layer 9 — API

**Status:** `/api/v1/rag/v2/search` + bulk `/ingest` exist.

| Endpoint | Status | Design |
|----------|--------|--------|
| `POST /api/v1/rag/v2/search` | ✅ exists | Hybrid search + rerank; add filters (chain, domain, date), domain routing param |
| `POST /api/v1/rag/v2/ingest` | ✅ exists | Envelope-based; extend for `content_type`-aware chunking + async batch ack |
| `POST /api/v1/rag/v2/ask` | 🚧 P0 | Grounded Q&A: search → context → LLM → cited answer + sources; fail-closed "no evidence" |
| `POST /api/v1/rag/v2/ask/stream` | 🚧 P1 | SSE streaming (token-by-token), first-token latency target < 1.5s; x402 payment-gating pattern (docs/adr/005) applies for paid tiers |
| `GET /api/v1/rag/v2/collections` | 🚧 P1 | Collection taxonomy + freshness + point counts (ops surface) |

- **Auth**: existing unified auth + x402 payment gate for premium
  endpoints (ADR-005 pattern — RAG is a natural x402 candidate).
- **Pagination & limits**: top_k ≤ 50, query ≤ 2048 chars (already in
  `SearchRequest`).
- **Streaming**: SSE over the LangGraph stream for `ask`; no WebSocket needed
  for request/response Q&A.

---

## 4. Cross-Cutting Concerns

### 4.1 Security
- No secrets in git (gitleaks CI gate already in this repo); Infinity/Qdrant
  credentials via env/gopass, never in code.
- Prompt-injection guard on ingested web content (adversarial instructions
  inside scraped pages) — strip instruction-like blocks at chunk time, flag
  in metadata, and system-prompt "content is untrusted data, not commands".
- Input sanitization on queries (this repo's standards apply; RAG is a
  public-facing LLM surface = treat every query as untrusted).
- Compliance: OFAC/sanction filter before agent tool-calls on addresses.

### 4.2 Scalability
- Embedding/rerank via Infinity is horizontally scalable (Docker, GPU
  optional) — the fleet already runs it as `infinity-rag:8001`.
- Qdrant: shard by collection; read replicas for hot news collections.
- Redis: cache-hit ratio target > 60% for news queries (high query
  repetition in crypto news cycles).
- Backfill runs at 200 docs/min cap; batch backfills schedule off-peak.

### 4.3 Cost (Frugality Principle — target < $3/month marginal)
- Self-hosted Infinity + Qdrant + Redis = $0 marginal.
- Free-tier data producers (existing list): CoinGecko, DefiLlama, Etherscan,
  Helius, Alchemy, SearXNG (self-hosted), n8n workflows.
- LLM: free layers first (Ollama local, OpenCode Zen, Groq, OpenRouter FREE,
  Gemini free); paid fallback only for revenue-generating endpoints behind
  x402.

---

## 5. Integration Points with Sibling Systems

| Sibling | Integration | Direction |
|---------|-------------|-----------|
| **RugMunchScan** | Findings → `scanner`/`scam_intel` collections via webhook + `FeedbackRecord`; `/ask` can trigger re-scan tool | scan → RugBrain |
| **lite-chain-indexer** | On-chain events (transfers, mints, LP changes) → `onchain`/`whale_movements`; real-time queries route to indexer RPC | indexer → RugBrain |
| **pryscraper** | Crawl4AI pipeline feeds web/news ingestion; reuses 3-layer extraction stack (trafilatura → FlareSolverr → Chromium) | pryscraper → RugBrain |
| **rmi-rag (sibling repo)** | Keep as standalone reference/edge deploy; RugBrain in rmi-backend is the production path. Avoid duplicate pipelines (RAG_MODERNIZATION.md flagged 4 overlapping pipelines — consolidate INTO RugBrain, don't add a 5th) | convergence |
| **rmi-rag-wt-ragas** | Ragas harness → formalized as RugBrain L7 eval suite | eval |
| **Knowledge Graph** (`app/graph_rag.py`) | Community/narrative expansion at retrieval time | KG → retrieval |
| **rmi-codebase-mcp** | Security pattern library (ChromaDB, 14 categories) as a retrieval source for Security domain queries | pattern KB → RugBrain |

---

## 6. Current State vs Target (honest gap matrix)

| Capability | Current | Target (P0) | Gap |
|------------|---------|-------------|-----|
| Hybrid retrieval + rerank | ✅ (RRF fusion, alpha 0.7, bge-reranker-v2-m3 fail-open) | same | none |
| Chunking | ✅ 512-char recursive | token-aware 512-token per content type | AST chunker, semantic chunker |
| Sparse vectors | ❌ dense only | dense + sparse per point | Qdrant sparse vector config |
| Single embedding model | ⚠️ bge-m3 in new code, legacy 3-model mess | bge-m3 everywhere | migration job for legacy collections |
| `/ask` grounded Q&A | ❌ | ✅ P0 | new |
| Streaming | ❌ | ✅ P1 | new |
| Agentic orchestration | ❌ | ✅ P1 (LangGraph) | new |
| Graph-RAG fusion | ⚠️ graph_rag.py exists, not fused into retrieval | fused in P1 | wiring |
| Evaluation (Ragas) | ❌ | ✅ P0 | golden set + CI gate |
| Phoenix observability | ❌ (only unrelated Supabase "Phoenix channels" ref) | ✅ P1 | greenfield |
| News ingestion pipeline | ⚠️ app/domains/news/ingest.py exists, Qdrant-wired | consolidated + recency boost | wiring |
| Dedup + embed cache | ✅ dedup, ❌ embed cache | both | small |

---

## 7. Implementation Roadmap

### P0 — Foundation (ship `/ask`, one embedding model, hybrid everywhere)
1. **Consolidate collections & models**: migrate legacy 768/384-dim data to
   bge-m3 1024-dim; define the 18-collection taxonomy; tag collection config
   with model metadata. *(2-3 days)*
2. **Token-aware chunking**: 512-token target, 15% overlap, keep char
   fallback; wire quality gate extensions (spam ratio already configured).
   *(2 days)*
3. **Sparse vectors**: enable Qdrant sparse (BM25) alongside dense; RRF
   fusion already parameterized (k=60). *(2 days)*
4. **`POST /ask`**: search → context → LLM with citations + fail-closed
   "no evidence" branch. Reuse `ContextBuilder` (citations already carried).
   *(3-4 days)*
5. **Ragas eval harness + golden set v1**: 100 Q&A pairs (security-first),
   CI gate on RAG changes; wire into `rmi-rag-wt-ragas`. *(4 days)*
6. **Embedding cache** in Redis (`embed:{sha256}` → vector, TTL).
   *(1 day)*

**Exit criteria P0:** `/ask` answers 100-pair golden set with faithfulness
≥ 0.85; hybrid (dense+sparse+rerank) beats dense-only by ≥5 precision@5;
all collections at 1024-dim.

### P1 — Agentic + Observability
1. **LangGraph orchestrator**: router state machine (real-time → RPC tool,
   historical → vector, ambiguous → LLM), max 5-step agentic research,
   tool-use for lite-indexer + RugMunchScan APIs. *(1 week)*
2. **Phoenix (Arize)**: OpenInference auto-instrumentation + manual spans on
   HybridRetriever/rerank; latency + token budgets; n8n alert hook. *(2-3 days)*
3. **SSE streaming** for `/ask/stream`; x402 gate on premium endpoints.
   *(2-3 days)*
4. **Content-type chunkers**: tree-sitter AST chunker for Solidity/Rust/Move;
   semantic chunker for whitepapers/audit reports. *(4-5 days)*
5. **Graph-RAG fusion**: entity extraction on query → KG community expansion
   → augmented retrieval set. *(3-4 days)*
6. **News pipeline consolidation**: RSS/news/social ingest via pryscraper,
   recency-boosted scoring (`temporal_decay.py`), 90-day TTL. *(3-4 days)*

**Exit criteria P1:** agentic mode resolves 80% of a 50-query multi-step
research set without hitting the iteration cap; Phoenix dashboards live;
streaming first-token < 1.5s p95; news freshness < 10 min.

### P2 — Depth
1. **Golden set v2**: 300+ pairs across all domains; monthly online-signal
   curation from feedback loop. *(ongoing)*
2. **Fine-tuned reranker** on crypto audit data (or domain-adapted bge-reranker).
3. **Cross-collection federation**: one query → ranked answers across all 4
   domains with provenance (multi-domain dossier mode).
4. **On-chain event streaming**: live whale/narrative detection pushed into
   collections (webhook → ingest), narrative timeline synthesis.
5. **OSS packaging**: RugBrain as standalone Docker Compose stack (Infinity +
   Qdrant + Redis + API) with sample corpus — the OSS demo of RugMunch's
   methodology (per OPEN-CORE.md: code, not internal ops).
6. **Eval-driven model registry**: embedder/reranker A/B in CI; promotion
   only on golden-set lift.

---

## 8. ADR Summary (key decisions in this doc)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Name: **RugBrain** | Brand family, security DNA, zero collision, no overpromise |
| 2 | Orchestration: **LangGraph**, not LlamaIndex | Existing custom retrieval core stays; LangGraph only adds agentic state machine |
| 3 | Retrieval core: **keep `app/rag/retrieval.py`**, extend, don't rewrite | Already implements hybrid + 2-stage rerank + citations; abstraction churn is waste |
| 4 | Embedding: **bge-m3 only**, one model | Dimension mismatch silently breaks hybrid retrieval |
| 5 | Hybrid = **dense + sparse + RRF + cross-encoder** | Keyword precision for addresses/CVEs + semantic recall; RRF k=60 already standard |
| 6 | Qdrant = projection, **Postgres = system of record** | Rebuildable indexes; backups already 3-tier |
| 7 | `/ask` is **fail-closed**: no evidence → "no data" | Security domain must never hallucinate |
| 8 | Observability **fail-open** (like reranker) | Instrumentation must not take search down |
| 9 | Eval is a **CI gate**, not a report | RAG regressions merge-blocked, same as covgate |
| 10 | Consolidate the 4 legacy pipelines **into RugBrain** | Adding a 5th pipeline repeats the RAG_MODERNIZATION.md anti-pattern |
