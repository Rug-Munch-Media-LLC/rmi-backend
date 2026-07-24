# RMI RAG Strategy — Complete Architecture & Roadmap

**Date:** 2026-07-24  
**Author:** RMI Codebase Intelligence  
**Version:** v2.0

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [Embedder Selection](#2-embedder-selection)
3. [Vector Store Strategy](#3-vector-store-strategy)
4. [Ingestion Pipeline](#4-ingestion-pipeline)
5. [Retrieval Strategy](#5-retrieval-strategy)
6. [Backup + Permanence](#6-backup--permanence)
7. [Continuous Learning](#7-continuous-learning)
8. [Implementation Roadmap](#8-implementation-roadmap)

---

## 1. Current State Audit

### 1.1 Components Inventory

| Component | File(s) | Status | Notes |
|-----------|---------|--------|-------|
| **v3 M3 Engine** | `app/rag/` (10 files) | ✅ Active | Three-pillar search + ANN ingest |
| **CryptoEmbedder** | `app/crypto_embeddings/` (6 files) | ✅ Active | Multi-head embedder, hash fallback still present |
| **Legacy rag_service** | `app/rag_service/` (7 files) | ✅ Active | Parallel pipeline, overlaps with M3 |
| **Historical Scraper** | `app/rag_historical.py` | ✅ Active | Weekly: Rekt DB, Chainabuse, SlowMist, TRM |
| **Chunking** | `app/rag/chunking.py` + `app/rag_chunking.py` | ⚠️ DUAL | Two chunkers exist — M3 one (recursive) and standalone (strategy-based) |
| **Observability** | `app/rag_observability.py` | ✅ Active | Langfuse + local metrics fallback |
| **Evaluation** | `app/rag_evaluation.py` | ✅ Active | 50 golden test queries, RAGAS-style |
| **Permanence** | `app/rag_permanence.py` | ✅ Active | Cloudflare R2 cold storage |
| **Feedback Loop** | `app/rag_feedback.py` | ✅ Active | Scanner verdict → weight adjustment |
| **Agentic RAG** | `app/rag_agentic.py` | ✅ Active | LLM reranker + multi-hop + real-time monitor |
| **Supabase pgvector** | `app/supabase_vector.py` | ⚠️ FALLBACK | Tier-1 but often falls back to Redis |
| **LlamaIndex Adapter** | `app/rag/llama_adapter.py` | 🚧 FEATURE-FLAGGED | Not in production |
| **Embedding Router** | `app/embedding_router.py` | ⚠️ PARTIAL | Thin wrapper, rate-limiter not fully wired |
| **Scripts** | `scripts/unified_rag_ingest.py`, `rag_rebuild.py`, `populate_rag_collections.py`, `refresh_rag_ttls.py` | ✅ Active | Daily + one-shot pipelines |

### 1.2 Embedding Models Configured

| Model | Source | Dims | Cost | Status | Priority |
|-------|--------|------|------|--------|----------|
| `bge-m3` (via Ollama) | Local (Talos/Hydra) | 1024 | $0 | ✅ Primary | Tier 1 |
| `nvidia/llama-nemotron-embed-vl-1b-v2:free` | OpenRouter | 2048 | $0 (free tier) | ✅ Fallback | Tier 2 |
| `BAAI/bge-small-en-v1.5` | Local (sentence-transformers) | 384 | $0 (CPU) | ✅ Active | Tier 3 |
| `BAAI/bge-m3` (via HuggingFace) | HF Inference API | 1024 | $0 (free tier) | ⚠️ Unreliable | Tier 4 |
| `openai/text-embedding-3-large` | OpenAI API | 3072 | Paid | 🚫 Not wired | N/A |
| `qwen3-embedding:4b` (target) | Local (Ollama) | 2048 | $0 | 🚧 Migration target | Target |

### 1.3 Vector Stores Connected

| Store | Type | Status | Use Case |
|-------|------|--------|----------|
| **Redis** (primary) | In-memory ANN via numpy + Redis HASH | ✅ Active | All current search/ingest |
| **Supabase pgvector** | Postgres pgvector extension | ⚠️ Fallback | Tier-1 but often degraded |
| **Qdrant** (via LlamaIndex) | Dedicated vector DB | 🚧 Feature-gated | LlamaIndex path only |
| **FAISS** (legacy) | On-disk ANN | 🚧 Historic | Referenced in feedback, not actively used |

### 1.4 Data Sources Feeding RAG

| Source | Collection | Cadence | Quality |
|--------|-----------|---------|---------|
| GitHub scam DBs (ScamSniffer, MetaMask, CryptoScamDB) | `known_scams` | Daily | High (curated) |
| Rekt DB (3,000+ DeFi hacks) | `defi_hacks` | Weekly | High |
| Chainabuse scam reports | `rug_timeline` | Weekly | Medium (community) |
| SlowMist Hacked Archive | `defi_hacks` | Weekly | High |
| Rekt News post-mortems | `defi_hacks` | Weekly | High |
| Immunefi bug bounties | `vuln_patterns` | Weekly | High |
| CertiK audit findings | `vuln_patterns` | Weekly | High |
| TRM Labs crime report | `crime_reports` | Yearly | High |
| Elliptic scam report | `crime_reports` | Yearly | High |
| Etherscan labels (phish/hack) | `known_scams` | Ad-hoc | High |
| Solana token registry | `known_scams` | Ad-hoc | Medium |
| News articles | `news_articles` | Daily | Medium |
| SENTINEL scanner results | `token_analysis` | Real-time | Variable |
| Scam patterns (curated) | `scam_patterns` | Static | High |
| Contract audits | `contract_audits` | Static | High |
| Transaction patterns | `transaction_patterns` | Static | High |

### 1.5 Current Gaps

| # | Gap | Severity | Root Cause |
|---|-----|----------|------------|
| 1 | **Dual RAG pipelines** (M3 + rag_service) exist in parallel with no clear winner | HIGH | Historic refactoring left both active |
| 2 | **Hash-based fallback** is still used in `CryptoEmbedder._semantic_embed()` despite T04 ruling against it in `rag/embeddings.py` | HIGH | Inconsistent application of the policy |
| 3 | **Redis as primary vector store** does not scale past ~100K docs (in-memory, no disk persistence) | HIGH | pgvector integration is tier-1 but unreliable |
| 4 | **No cross-collection dedup** — same content can appear in `known_scams` and `scam_patterns` | MEDIUM | Dedup is per-collection only |
| 5 | **BM25-lite** in `engine.py` is a stopgap — real BM25/SPLADE not integrated | MEDIUM | `splade_bm25` referenced but not consistently wired |
| 6 | **Query transformation** in `search_with_transform` exists but not used by default `three_pillar_search` | MEDIUM | Two search paths diverged |
| 7 | **No embedding versioning** — if model changes, old vectors become unrecoverable | MEDIUM | `EMBEDDING_VERSION` env var exists but not enforced |
| 8 | **No chunk-level metadata inheritance** — parent doc metadata lost during chunking | LOW | Chunking strips most metadata |
| 9 | **Reranker only runs in agentic path** — main search path does not cross-encode | MEDIUM | Cost concern, but quality suffers |
| 10 | **No A/B testing framework** for embedding model changes | LOW | Migration is manual, no automated quality gate |

---

## 2. Embedder Selection

### 2.1 Available Models — Full Comparison

| Model | Dims | Source | Cost | Quality (MTEB) | Crypto-specific | Latency | RAM/GPU |
|-------|------|--------|------|----------------|-----------------|---------|---------|
| **bge-m3** (Ollama) | 1024 | Ollama (local) | $0 | 64.0 | Good (general) | ~50ms | ~2GB RAM |
| **qwen3-embedding:4b** (Ollama) | 2048 | Ollama (local) | $0 | ~66.0 | Better (Chinese crypto text) | ~80ms | ~3GB RAM |
| **nomic-embed-text** (Ollama) | 768 | Ollama (local) | $0 | 62.4 | Average | ~30ms | ~1GB RAM |
| **bge-large-en-v1.5** (Ollama) | 1024 | Ollama (local) | $0 | 63.8 | Good | ~100ms | ~5GB RAM |
| **text2vec-large-chinese** | 1024 | Ollama (local) | $0 | N/A | Excellent (CN scams) | ~120ms | ~4GB RAM |
| **all-MiniLM-L6-v2** | 384 | sentence-transformers | $0 | 61.0 | Poor (too small) | ~10ms | ~0.5GB |
| **Nemotron** (OpenRouter) | 2048 | OR API (free) | $0 | ~65.0 | Good | ~200ms | N/A (API) |
| **OpenAI text-embedding-3-large** | 3072 | OpenAI API | Paid | 65.9 | Best general | ~100ms | N/A (API) |

### 2.2 Recommendation: Qwen3-Embedding:4b (2026)

**Verdict:** Migrate from `bge-m3` (1024d) → `qwen3-embedding:4b` (2048d).

**Rationale:**
1. **Higher dimension** (2048 vs 1024) captures more nuance for crypto-specific terminology (scam patterns, contract bytecode features, wallet behaviors)
2. **Ollama-native** — runs locally, zero API cost, same infrastructure as current bge-m3
3. **Chinese + English bilingual** — critical for scam research (many scam write-ups are in Chinese on SlowMist, BlockSec, etc.)
4. **4B parameters** is the sweet spot for quality/speed on CPU
5. Already referenced in code as `rmi/qwen3-embedding:4b` in `DIMS` config — the migration infra exists in `app/rag/embedder.py`

**Migration Path (already partially implemented in `embedder.py`):**
```python
# Current in `app.rag.embedder`:
EmbedderBackend.BGE_M3 = "bge-m3"      # 1024d, legacy
EmbedderBackend.QWEN3_4B = "qwen3-embedding:4b"  # 2048d, target
```

- `reindex_collection()` handles per-collection migration
- Strategy: reindex one collection per night, verify with `rag_evaluation.py` before proceeding
- If nDCG@10 < 8% improvement, fall back and investigate

### 2.3 Embedding Architecture (Multi-Head)

The CryptoEmbedder already implements a proven multi-head pattern:

```
Input text
    │
    ├──→ Semantic head (bge-m3 / qwen3) → 1024d / 2048d vector
    ├──→ Code feature extractor → 128d structural features    
    ├──→ Transaction feature extractor → 64d behavioral features
    └──→ Wallet feature extractor → 64d entity features
              │
              ▼
         Concatenated vector (1280d or 2304d total)
```

**Recommendation:** Keep this multi-head pattern. It preserves the crypto-domain-specific feature engineering while upgrading the semantic backbone. No code changes needed to the concatenation logic.

---

## 3. Vector Store Strategy

### 3.1 Three-Tier Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    TIER 1: Redis (Hot)                    │
│  • ~65K vectors in memory via numpy matrix               │
│  • Sub-millisecond search (in-process)                   │
│  • Perfect for: real-time scanner, agent queries         │
│  • Downside: no persistence on restart (rebuilt from     │
│    Supabase or R2)                                       │
├──────────────────────────────────────────────────────────┤
│                    TIER 2: Supabase pgvector (Warm)       │
│  • HNSW-indexed Postgres table                           │
│  • Sub-20ms search at 1M+ vectors                        │
│  • Perfect for: batch analytics, production search       │
│  • Currently degraded — needs reliable connection        │
├──────────────────────────────────────────────────────────┤
│                    TIER 3: Cloudflare R2 (Cold)           │
│  • JSON snapshot backups of all collections              │
│  • Free tier: 10GB storage, 10M ops/month                │
│  • Perfect for: disaster recovery, model migration       │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Current State vs. Target

| Aspect | Current | Target |
|--------|---------|--------|
| Primary store | Redis (numpy in-process) | Supabase pgvector (HNSW) |
| Hot cache | Redis HASH ops | Redis + numpy (keep as cache) |
| Cold backup | R2 snapshots ✅ | Same (works) |
| Scaling ceiling | ~100K vectors (memory) | ~10M vectors (pgvector) |

### 3.3 Indexing Strategy

**pgvector HNSW (target):**
```sql
CREATE INDEX ON rag_vectors USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 200);
```
- Already implemented in `app/supabase_vector.py` — `build_hnsw_index()`
- Falls back to IVFFlat if HNSW unavailable
- No training step needed (unlike IVFFlat)

**Redis ANN (current, keep as cache layer):**
- In-process numpy matrix with cosine similarity
- L2-normalized on insert
- `argpartition` for O(n log k) top-k search
- Good for: sub-millisecond queries on small collections (<50K)

### 3.4 Collection Organization

| Collection | Tier | Purpose | Est. Size | Retention |
|-----------|------|---------|-----------|-----------|
| `scam_patterns` | Redis+pgvector | Known scam code patterns | ~50 docs | Permanent |
| `known_scams` | Redis+pgvector | Address-level scam reports | ~10K docs | Permanent |
| `defi_hacks` | Redis+pgvector | DeFi exploit incidents | ~3K docs | Permanent |
| `rug_timeline` | Redis+pgvector | Scam timeline narratives | ~2K docs | Permanent |
| `vuln_patterns` | Redis+pgvector | Vulnerability patterns | ~1K docs | Permanent |
| `crime_reports` | Redis+pgvector | Annual crime reports | ~50 docs | Permanent |
| `contract_audits` | Redis+pgvector | Audit checklist items | ~25 docs | Permanent |
| `transaction_patterns` | Redis+pgvector | Tx behavior patterns | ~25 docs | Permanent |
| `forensic_reports` | Redis+pgvector | Post-mortem analyses | ~500 docs | Permanent |
| `wallet_profiles` | Redis only | Wallet metadata | ~10K docs | 1 year |
| `token_analysis` | Redis only | GoPlus scan results | ~100K docs | 90 days |
| `news_articles` | Redis only | News content | ~5K docs | 30 days |
| `market_intel` | Redis only | Market intelligence | ~1K docs | 1 year |

**Key principle:** Security data (patterns, hacks, audits) is permanent. Temporal data (news, scans) auto-expires via Redis TTL.

---

## 4. Ingestion Pipeline

### 4.1 Pipeline Architecture

```
External Sources
    │
    ├── GitHub (ScamSniffer, MetaMask, CryptoScamDB) ──┐
    ├── Historical (Rekt DB, Chainabuse, SlowMist) ────┤
    ├── SENTINEL Scanner (real-time token scans) ──────┤
    ├── News Service (crypto news articles) ───────────┤
    └── Research (TRM, Elliptic, Immunefi, CertiK) ────┘
    │                                                     │
    ▼                                                     ▼
┌───────────────────────────────────────────────────────────┐
│                 UNIFIED INGEST PIPELINE                     │
│                                                           │
│  1. Fetch raw data from source                            │
│  2. Quality filter (score ≥ 30, min length ≥ 50 chars)   │
│  3. SHA-256 content dedup (per-collection)                │
│  4. Chunk (content-type-aware strategy)                   │
│  5. Embed (multi-head, with cache)                        │
│  6. Store (Redis ANN + pgvector + R2 snapshot)           │
│  7. Update dedup registry                                 │
└───────────────────────────────────────────────────────────┘
```

### 4.2 Chunking Strategy (per content type)

| Content Type | Strategy | Chunk Size | Overlap | Notes |
|-------------|----------|-----------|---------|-------|
| `news` | Recursive | 512 chars | 15% | Standard |
| `scam_report` | Semantic | 400 chars | 10% | Preserve narrative context |
| `contract_code` | Code-aware | 256 chars | 20% | Split at class/function boundaries |
| `wallet_profile` | Fixed | 128 chars | 0% | Short, structured data |
| `annual_report` | Recursive | 800 chars | 15% | Longer chunks for dense reports |
| `default` | Recursive | 512 chars | 15% | General fallback |

The dual chunking system (`app/rag/chunking.py` vs `app/rag_chunking.py`) must be **consolidated** — `app/rag_chunking.py` is more mature (has content-type strategy presets, quality scoring, batch processing).

### 4.3 Ingestion Cadence

| Source | Cadence | Collection | Max per cycle |
|--------|---------|-----------|---------------|
| GitHub scam DBs (3 sources) | Daily at 3AM | `known_scams` | 50 |
| Rekt DB | Weekly Sunday 2AM | `defi_hacks` | 500 |
| Chainabuse | Weekly | `rug_timeline` | 200 |
| SlowMist | Weekly | `defi_hacks` | 200 |
| Rekt News | Weekly | `defi_hacks` | 50 |
| Immunefi | Weekly | `vuln_patterns` | 100 |
| CertiK | Weekly | `vuln_patterns` | 100 |
| TRM/Elliptic | Yearly | `crime_reports` | Full report |
| News articles | Daily | `news_articles` | 20 |
| SENTINEL scanner | Real-time | `token_analysis` | Unlimited (backpressured) |

### 4.4 Backpressure

Already implemented in `scripts/unified_rag_ingest.py`:
- Max 50 docs per cycle
- Min 2s between API calls
- Redis lock: `rmi:rag:ingest:last_run` (1h TTL, prevents concurrent runs)
- Redis memory check: stops if >1.5GB
- TTL-based auto-expiry for temporal collections

**Recommendation:** Add queue-based backpressure for real-time scanner feed (currently unbounded).

### 4.5 Deduplication

Current (good, keep):
- SHA-256 content hash per chunk
- Per-collection dedup registry in Redis: `rag:hashes:{collection}`
- 7-day TTL on dedup keys (prevents re-ingestion within window)

Gap to fix:
- **Cross-collection dedup** — same address can appear in both `known_scams` and `scam_patterns`
- Fix: add global dedup key `rag:global_dedup:{content_hash}` in addition to per-collection

---

## 5. Retrieval Strategy

### 5.1 Three-Pillar Search (Current)

```
User Query
    │
    ├── Pillar 1: ANN Vector Search (semantic)
    │     → embed query → cosine similarity on numpy matrix → top-K*3 hits
    │
    ├── Pillar 2: BM25-lite Keyword Search (lexical)  
    │     → term frequency scoring on Redis docs → top-K*3 hits
    │
    └── Pillar 3: Metadata Filter (structured)
          → chain=, severity=, source=
          → Applied after fusion
    │
    ▼
Reciprocal Rank Fusion (k=60)
    │
    ▼
Top-K results
```

**Issues with current implementation:**
1. BM25-lite is a naive TF scorer, not real BM25 — missing IDF, field weighting, query normalization
2. Metadata filtering is post-fusion rather than pre-fusion (loses potential candidates)
3. No re-ranking step in the main path (only in agentic path)
4. Pillar weights are static for `three_pillar_search` in `engine.py` but dynamic in `rag_service/retrieval.py`

### 5.2 Target Retrieval Strategy

```
User Query
    │
    ├── Query Classification (entity_heavy / keyword_heavy / semantic_heavy / balanced)
    │
    ├── Pillar 1: ANN Vector Search (pgvector HNSW)
    │     → embed query → pgvector cosine distance → top-K*5 hits
    │     → Include metadata pre-filter in SQL
    │
    ├── Pillar 2: SPLADE/BM25 Sparse Search
    │     → `app/splade_bm25` or pgvector FTS → top-K*5 hits
    │
    └── Pillar 3: Entity + Knowledge Graph
          → Extract entities (addresses, tokens, chain names)
          → Lookup in entity index + KG expansion
          → top-K*3 hits
    │
    ▼
Dynamic RRF Fusion (adaptive k, pillar weights by query type)
    │
    ├── MMR Deduplication (λ=0.6, remove redundancy)
    │
    ├── Cross-Encoder Reranker (LLM)
    │     → Score top 20 with LLM → return top-K
    │     → Optional: skip for latency-sensitive paths
    │
    ├── Parent-Child Context Expansion
    │     → Attach parent document context for short chunks
    │
    └── Confidence Scoring
          → Evidence weighting per source type
          → Confidence score 0-1 returned with results
    │
    ▼
Final results (with provenance tracking)
```

### 5.3 Query Transformation

The `search_with_transform` function exists but is not wired into the default path:

```python
# Already implemented:
query_transforms = [
    "original_query",
    "keyword_extraction",    # Extract key crypto terms
    "entity_expansion",      # Expand token symbols, addresses
    "chain_specific",        # Add chain context
    "temporal",              # Add time context for recent events
]
```

**Action:** Wire query transformation into the main `three_pillar_search` path with a lightweight strategy selector.

### 5.4 Reranking Strategy

| Level | Method | Cost | When | Quality Impact |
|-------|--------|------|------|---------------|
| 1 | Vector similarity (cosine) | $0 | Always | Baseline |
| 2 | MMR dedup | $0 | Always | +15% diversity |
| 3 | Cross-encoder (LLM) | ~$0.001/query | Agentic queries | +30% precision |
| 4 | Multi-hop retrieval | ~$0.01/query | Deep investigation | +50% recall |

**Recommendation:** 
- Always run Level 1+2 (free, high impact)
- Run Level 3 for scanner queries (high precision needed)
- Run Level 4 for user-facing investigation (agentic path)

---

## 6. Backup + Permanence

### 6.1 Current Architecture (Works Well)

```
Hot (Redis) ───→ Warm (7-day local) ───→ Cold (R2 permanent)
```

- `app/rag_permanence.py` handles all of this
- R2 free tier: 10GB storage, 10M ops/month — sufficient for ~5 years of snapshots
- Snapshot format: chunked JSON files (5MB max per chunk)
- `nightly_cycle()` runs snapshot + local cache cleanup
- Restore via `restore_all()` downloads from R2 and writes to Redis

### 6.2 Snapshot Strategy

| Component | Cadence | Retention | Size |
|-----------|---------|-----------|------|
| R2 snapshots | Nightly | All versions (append-only) | ~500MB total |
| Local cache | Nightly | 7 days | ~50MB |
| Embedding version tag | Per snapshot | Forever | Tiny |

### 6.3 Current Gaps

| Gap | Impact | Fix |
|-----|--------|-----|
| No pgvector backup | If Supabase is primary, R2-only Redis snapshots won't restore pgvector | Add pgvector table dump to `snapshot_all()` |
| No index backup | ANN indexes (numpy matrix) are rebuilt, not backed up | Serialize numpy matrix to R2 alongside JSON docs |
| No automatic restore test | We don't know backups work until needed | Add weekly restore test similar to `backup-restore-test.sh` |
| Snapshots not compressed | R2 storage used inefficiently | Add gzip compression before upload (text JSON compresses 10:1) |

### 6.4 Disaster Recovery Plan

```
Scenario 1: Redis crashes (no persistence)
  1. Rebuild numpy matrix from Redis data (if disk persistence survives) 
  2. OR restore from R2: restore_all()
  3. OR rebuild from pgvector (if connected)
  
Scenario 2: Full infrastructure loss
  1. Restore R2 snapshots → new Redis instance
  2. Rebuild ANN indexes from restored documents
  3. Verify with rag_evaluation.py golden tests

Scenario 3: Embedding model change
  1. Use snapshots tagged with old EMBEDDING_VERSION
  2. Re-embed all documents with new model
  3. Verify quality with rag_evaluation.py before cutover
```

---

## 7. Continuous Learning

### 7.1 Feedback Loop Architecture

```
SENTINEL Scanner
    │
    ├── Confirms scam → app/rag_feedback.py
    │     → Boost matching doc weights (+0.3 * confidence)
    │     → Similar patterns get priority in future searches
    │
    └── False positive (cleared) → app/rag_feedback.py
          → Penalize matching doc weights (-0.2 * confidence)
          → Fewer false alarms over time
```

### 7.2 Quality Metrics

Collected by `app/rag_observability.py` + Langfuse:

| Metric | Source | Target | Alert Threshold |
|--------|--------|--------|-----------------|
| Embedding latency | `trace_embedding()` | <100ms avg | >500ms |
| Retrieval latency | `trace_retrieval()` | <200ms avg | >1s |
| Cache hit rate | `_cache_hits / (_cache_hits + _cache_misses)` | >60% | <30% |
| Evaluation score | `rag_evaluation.py` (50 tests) | >0.7 avg | <0.5 |
| Pass rate | `rag_evaluation.py` | >80% | <60% |
| Ingest throughput | `trace_ingest()` | N/A | 0 for >24h |

### 7.3 Evaluation Golden Set

`app/rag_evaluation.py` has 50 test queries across 13 collections. Each query checks:
1. **Context precision** (60% weight) — do expected terms appear in results?
2. **Context recall** (40% weight) — did we get enough results?

**Recommendation:** Expand to 100+ tests, add adversarial queries (edge cases, ambiguous terms, cross-chain queries).

### 7.4 Agent-Driven Improvement

The feedback loop should extend to agent interactions:

```
Agent asks question → RAG returns results
    → Agent marks results as helpful/unhelpful (implicit feedback)
    → Low-confidence results trigger:
        a) Query reformulation (already in query_transform)
        b) Expanded search across more collections
        c) Feedback to ingestion pipeline ("we need docs about X")
```

**This is the highest-leverage improvement:** agents are the heaviest RAG consumers. Their implicit feedback is the richest signal we have.

---

## 8. Implementation Roadmap

### Phase 1: Fix Current Gaps (Weeks 1-3)

| # | Task | Files Affected | Effort | Priority |
|---|------|---------------|--------|----------|
| 1 | **Consolidate dual pipelines** — deprecate `rag_service` in favor of M3 engine | `rag_service/*` → `rag/*` | 3 days | 🔴 Critical |
| 2 | **Remove hash fallback** from `CryptoEmbedder._semantic_embed()` — fail loud per T04 | `crypto_embeddings/embedder.py` | 1 day | 🔴 Critical |
| 3 | **Consolidate dual chunkers** — keep `app/rag_chunking.py` as the standard | `rag/chunking.py` merge into `rag_chunking.py` | 1 day | 🟡 High |
| 4 | **Fix pgvector connection reliability** — add retry, circuit breaker | `supabase_vector.py` | 2 days | 🟡 High |
| 5 | **Wire query transformation** into default `three_pillar_search` | `rag/engine.py`, `rag_service/retrieval.py` | 1 day | 🟡 High |
| 6 | **Add cross-collection dedup** (global content hash) | `rag_chunking.py`, `rag/engine.py` | 1 day | 🟡 High |
| 7 | **Compress R2 snapshots** with gzip | `rag_permanence.py` | 0.5 day | 🟢 Medium |

### Phase 2: Productionize (Weeks 4-6)

| # | Task | Files Affected | Effort | Priority |
|---|------|---------------|--------|----------|
| 8 | **Migrate from bge-m3 to qwen3-embedding:4b** — one collection/night, verify each morning | `rag/embedder.py` | 5 days | 🔴 Critical |
| 9 | **Make pgvector the primary store** — Redis becomes cache layer only | `supabase_vector.py`, `rag/engine.py` | 3 days | 🔴 Critical |
| 10 | **Replace BM25-lite with real SPLADE/BM25** | `rag/engine.py` or wire `splade_bm25` | 2 days | 🟡 High |
| 11 | **Cross-encoder reranker in main path** (cost-gated) | `rag_agentic.py` → `rag/engine.py` | 2 days | 🟡 High |
| 12 | **Add weekly pgvector snapshot** to R2 backup | `rag_permanence.py` | 1 day | 🟡 High |
| 13 | **Embedding version enforcement** — reject queries with wrong version | `rag/models.py`, `rag/embeddings.py` | 1 day | 🟢 Medium |
| 14 | **Pre-filter metadata** in ANN search (not post-fusion) | `rag/engine.py`, `supabase_vector.py` | 1 day | 🟢 Medium |

### Phase 3: Advanced Features (Weeks 7-10)

| # | Task | Effort | Priority |
|---|------|--------|----------|
| 15 | **Agent interaction feedback loop** — track helpfulness per result | 4 days | 🔴 Critical |
| 16 | **Expand evaluation set** to 100+ golden queries | 2 days | 🟡 High |
| 17 | **Continuous embedding fine-tuning** on crypto scam corpus | 5 days | 🟡 High |
| 18 | **Automated A/B testing** for embedding model changes | 3 days | 🟡 High |
| 19 | **Real-time index updates** via streaming scanner feed | 3 days | 🟡 High |
| 20 | **Hybrid search with knowledge graph** — entity-aware retrieval | 5 days | 🟢 Medium |
| 21 | **Multi-modal RAG** — integrate image analysis (scam screenshots) | 5 days | 🟢 Medium |
| 22 | **Self-improving chunking** — learn optimal chunk size per content type | 3 days | 🟢 Medium |

### Phase 1 Detailed Action Plan

**Week 1 — Pipeline Consolidation:**
1. Audit all consumers of `rag_service` → redirect to `rag` module
2. Create deprecation shim in `rag_service/__init__.py` that logs warnings
3. Remove hash fallback from `CryptoEmbedder` (update `_semantic_embed` to mirror `rag/embeddings.py` fail-loud behavior)
4. Merge chunkers: move content-type presets + quality scoring into `app/rag/chunking.py` (the M3 one), deprecate `rag_chunking.py`

**Week 2 — Search Quality:**
1. Wire `query_transform` into `three_pillar_search` as optional pre-step
2. Add global dedup keys (`rag:global_dedup:{hash}`)
3. Fix pgvector health check — add exponential backoff, don't silently fall back
4. Add gzip to R2 snapshots (`gzip.compress()` before upload)

**Week 3 — Testing + Validation:**
1. Run full evaluation on consolidated pipeline → compare against baseline
2. Validate no regression in any collection
3. Deploy consolidated pipeline behind feature flag
4. Cut over production traffic

---

## Appendix A: File Map

```
app/rag/                          # v3 M3 Engine (active, primary)
  ├── __init__.py                 # Public API, health check
  ├── ann_index.py                # NumPy + Redis ANN index
  ├── chunking.py                 # Recursive chunking (M3 version)
  ├── embedder.py                 # Dual-dim embedder (migration wrapper)
  ├── embeddings.py               # Two-tier embedder (FAIL LOUD)
  ├── engine.py                   # Three-pillar search + ingest
  ├── llama_adapter.py            # LlamaIndex adapter (feature-flagged)
  ├── migration_router.py         # Legacy vs LlamaIndex router
  ├── models.py                   # Pydantic v2 models
  └── service.py                  # HTTP facade

app/crypto_embeddings/            # Multi-head crypto embedder
  ├── __init__.py                 # Re-exports
  ├── models.py                   # DIMS, HEAD_DEFAULTS, COLLECTIONS
  ├── features.py                 # Contract/tx/wallet feature extractors
  ├── providers.py                # Ollama, OpenRouter, HF, Local BGE
  ├── embedder.py                 # CryptoEmbedder (multi-head)
  └── seed.py                     # KNOWN_SCAM_PATTERNS

app/rag_service/                  # Legacy (to be deprecated)
  ├── __init__.py                 # Re-exports
  ├── cache.py                    # Redis connection
  ├── detection.py                # Scam pattern detection
  ├── indexing.py                 # Document ingestion
  ├── retrieval.py                # Three-pillar + MMR + reranker
  ├── state.py                    # Shared state
  └── stats.py                    # Statistics

app/rag_chunking.py               # Content-type aware chunking (keep this)
app/rag_evaluation.py             # 50 golden test queries
app/rag_observability.py          # Langfuse + local metrics
app/rag_permanence.py             # R2 cold storage
app/rag_agentic.py                # LLM reranker + multi-hop
app/rag_feedback.py               # Scanner feedback loop
app/rag_metrics_api.py            # Metrics endpoint
app/rag_historical.py             # Weekly historical ingestion
app/rag_tool.py                   # OpenClaw agent tool
app/embedding_router.py           # Rate-limited dispatcher
app/supabase_vector.py            # pgvector store (Tier-1 target)
app/github_rag_feeder.py          # GitHub scam sources

scripts/
  ├── unified_rag_ingest.py       # Daily GitHub + news ingest
  ├── populate_rag_collections.py # Static pattern seeding
  ├── rag_rebuild.py              # Full rebuild from CSVs
  └── refresh_rag_ttls.py         # TTL policy refresh
```

## Appendix B: Collection Summary

| Collection | Dims | Store | TTL | Size Target | Primary Source |
|-----------|------|-------|-----|-------------|---------------|
| scam_patterns | 2304 | Redis+Pg | ∞ | 50 | Curated |
| known_scams | 2304 | Redis+Pg | ∞ | 10K+ | GitHub + Etherscan |
| defi_hacks | 1024 | Redis+Pg | ∞ | 3K+ | Rekt DB + SlowMist |
| rug_timeline | 1024 | Redis+Pg | ∞ | 2K+ | Chainabuse |
| vuln_patterns | 1024 | Redis+Pg | ∞ | 1K+ | Immunefi + CertiK |
| crime_reports | 1024 | Redis+Pg | ∞ | 50 | TRM + Elliptic |
| contract_audits | 1024 | Redis+Pg | ∞ | 25 | Curated |
| transaction_patterns | 1024 | Redis+Pg | ∞ | 25 | Curated |
| forensic_reports | 1024 | Redis+Pg | ∞ | 500 | Rekt |
| wallet_profiles | 1280 | Redis | 365d | 10K+ | Etherscan |
| token_analysis | 1280 | Redis | 90d | 100K+ | GoPlus |
| news_articles | 1024 | Redis | 30d | 5K+ | News service |
| market_intel | 1024 | Redis | 365d | 1K+ | Aggregator |

## Appendix C: Key Principles

1. **FAIL LOUD, not silently wrong** — Hash fallback is forbidden. When no embedding backend responds, return 503, not gibberish.
2. **Security data is permanent** — Scam patterns, hack reports, audit findings never expire.
3. **Temporal data self-cleans** — News (30d), token scans (90d), wallet profiles (365d) auto-expire via TTL.
4. **Free first** — All embedding runs on local Ollama. OpenRouter is backup. Only use paid models when quality evaluation demands it.
5. **Measure before migrating** — Every model change must pass `rag_evaluation.py` with <10% regression on the golden test set.
6. **Agents are the best feedback source** — The heaviest RAG consumers produce the richest quality signal. Harvest it.
7. **Backup everything** — R2 nightly snapshots of all collections. Weekly pgvector dumps. Embedding version tagged.

---

*This document is a living strategy. Update as the RAG system evolves.*
