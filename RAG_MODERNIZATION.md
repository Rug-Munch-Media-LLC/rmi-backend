# RMI RAG Modernization — 2026 Standards

> **SUPERSEDED (2026-09-20):** this is the pre-modernization assessment.
> The stack it describes (nomic/bge-m3/bge-small, 4 overlapping pipelines,
> Infinity) is gone. Live: RugBrain as the canonical RAG service, ONE stack
> (`Qwen3-Embedding-4B` 2560d + `Qwen3-Reranker-4B`, DeepInfra + local
> llama.cpp), collections per `rugbrain/docs/COLLECTION_REGISTRY.md`.

# ======================================
# Design document for upgrading RMI's RAG system to production-grade
# modern standards. Based on audit of all 40+ endpoints, 4 pipelines,
# 9 collections, and 3 embedders.

## Current State Audit

### Collections (crypto_embeddings.py)
wallet_profiles, token_analysis, scam_patterns, forensic_reports,
market_intel, contract_audits, known_scams, news_articles,
transaction_patterns

### Embedders (INCONSISTENT — 3 different models)
- nomic-embed-text (768d) — rag_engine.py, smart_ai_engine.py
- bge-m3 (1024d) — rag_ingestion.py, rag_supreme.py
- bge-small-en-v1.5 (384d) — crypto_embeddings.py (primary)

### Pipelines (4 separate, overlapping)
- rag_engine.py — Qdrant REST API, nomic-embed-text, 5 collections
- rag_service.py — FAISS ANN, bge-small, 9 collections, 3-pillar search
- rag_supreme.py — 15-win pipeline, bge-m3, 5 Qdrant collections
- rag_firehose.py — continuous ingestion engine (designed, not fully wired)

### Gaps Identified
1. NO historical scam ingestion (Rekt DB, Chainabuse, DeFi hacks)
2. NO structured chunking — raw text embedding, no overlap
3. NO evaluation running (RAGAS mentioned, not active)
4. Embedding model inconsistency across pipelines
5. Firehose sources not wired (cadences defined, fetchers missing)
6. NO query transformation in production path
7. NO feedback loop active
8. Redis SCARD bug (FIXED 2026-06-17)
9. FAISS disk indexes exist but Redis backing data evicted for 7/9 collections

## Modern Standards (2025-2026 Industry Consensus)

### 1. Chunking Strategy
- DEFAULT: Recursive character splitting, 512 tokens, 15% overlap
- For code: add class/function boundary separators
- For news: sentence-based chunking preserves coherence
- For scam reports: semantic chunking on topic boundaries
- Overlap: 10-20% (test for your domain — some studies show no benefit)

### 2. Embedding Models
- STANDARDIZE on bge-m3 (1024d) — best open-source, multilingual
- Fallback: bge-small-en-v1.5 (384d) for fast/local
- Multi-head: different dims for different content types
  - Contract code: 128d structural features (already in crypto_embeddings.py)
  - Scam patterns: 384d behavioral embedding
  - News/articles: 1024d semantic (bge-m3)
  - Wallet profiles: 64d behavioral fingerprint

### 3. Retrieval Architecture
- HYBRID: Dense (70%) + BM25/Sparse (30%) — 5-15% recall improvement
- RRF fusion (Reciprocal Rank Fusion) — proven best for hybrid
- Cross-encoder rerank: top-20 → rerank → top-5
- MMR dedup: remove near-duplicate results
- Query expansion: generate 3 variants, fuse results

### 4. Ingestion Pipeline (UNIFIED)
- SINGLE entry point: POST /api/v1/rag/ingest
- Pipeline: Parse → Chunk → Dedup → Classify → Embed → Store → Index
- Dedup: content hash in Redis (MD5 of normalized text)
- Quality filter: skip docs below quality threshold
- Rate limiting: per-collection docs/minute
- Batch embedding: groups of 25-50, async

### 5. Historical Data Sources (NEW)
- Rekt DB (de.fi/rekt-database) — 3,000+ DeFi hacks since 2020
- Chainabuse — scam reports with addresses
- TRM Labs Crypto Crime Report — annual typologies
- Elliptic State of Crypto Scams — annual report
- Chainalysis Crypto Crime Report — annual trends
- SlowMist Hacked Archive — detailed exploit analysis
- Immunefi Bug Bounty Reports — vulnerability patterns
- CertiK Audit Findings — smart contract vulnerabilities
- Solana Compromised Accounts — known drained wallets
- Etherscan Labels — 115K+ labeled addresses (already have)

### 6. Evaluation Framework
- RAGAS metrics: faithfulness, answer_relevancy, context_precision, context_recall
- Golden test set: 50 known scam queries with expected answers
- Run weekly, alert on regression
- Track: Hit@5, MRR, NDCG@10

### 7. Feedback Loop
- Scanner hits → boost source weight
- False positives → penalize
- User corrections → update embeddings
- Track helpful docs, boost in future searches

## Implementation Plan

### Phase 1: Standardize & Consolidate (NOW)
1. Standardize embedder: bge-m3 (1024d) primary, bge-small (384d) fallback
2. Add recursive chunking to ingest pipeline
3. Wire firehose sources (Rekt DB, Chainabuse, Etherscan labels)
4. Add content hash dedup to all ingestion paths

### Phase 2: Historical Data Ingestion (THIS WEEK)
5. Build Rekt DB scraper → forensic_reports collection
6. Build Chainabuse scraper → known_scams collection
7. Ingest TRM/Elliptic/Chainalysis annual reports → market_intel
8. Ingest SlowMist/Immunefi/CertiK findings → contract_audits

### Phase 3: Evaluation & Feedback (NEXT WEEK)
9. Activate RAGAS evaluation pipeline
10. Build golden test set (50 queries)
11. Wire feedback loop (scanner hits → boost)
12. Add query transformation (HyDE, expansion)

### Phase 4: Advanced Retrieval (ONGOING)
13. Cross-encoder reranking (bge-reranker-v2-m3)
14. Parent-child retrieval for long documents
15. Multi-modal: code + text + transaction patterns
16. Streaming response for agentic investigation

## New Unified Ingestion Pipeline

```
POST /api/v1/rag/ingest
{
  "documents": [...],
  "collection": "known_scams",
  "source": "rekt_db",
  "chunking": "recursive"  // or "semantic", "sentence", "none"
}

Pipeline:
1. PARSE — extract text, metadata, entities
2. CHUNK — recursive split (512 tokens, 15% overlap)
3. DEDUP — MD5 hash check against Redis
4. QUALITY — score content, skip if < threshold
5. CLASSIFY — route to correct collection
6. EMBED — batch embed via bge-m3 (Infinity)
7. STORE — Redis (hot) + FAISS (index) + R2 (cold)
8. INDEX — update ANN index version
```

## New Collections to Add

| Collection | Source | Dims | Purpose |
|-----------|--------|------|---------|
| defi_hacks | Rekt DB, SlowMist | 1024d | Historical DeFi exploits |
| rug_timeline | Chainabuse, SENTINEL | 1024d | Rug pull chronology |
| vuln_patterns | Immunefi, CertiK | 1024d | Smart contract vulnerabilities |
| crime_reports | TRM, Elliptic, Chainalysis | 1024d | Annual crime typologies |
| compromised_wallets | Solana, Etherscan | 384d | Known drained addresses |
| exploit_techniques | All sources | 1024d | How hacks were executed |

## Success Metrics

- RAG total_docs: 2,473 → 50,000+ (20x)
- Collections with data: 2/9 → 9/9 + 6 new
- Embedding consistency: 3 models → 1 primary + 1 fallback
- Ingestion cadence: ad-hoc → continuous (firehose)
- Evaluation: none → weekly RAGAS
- Chunking: none → recursive 512-token
- Dedup: none → content hash
- Cold storage: partial → full R2 permanence
