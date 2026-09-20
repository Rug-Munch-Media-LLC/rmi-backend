# Qdrant RAG Vector-Store Consolidation — Design

Status: DRAFT · Owner: RMI Engineering · Date: 2026-08-13 · READ-ONLY design (no code changed)
Target: Qdrant 1.18.3 (docker `rmi-qdrant`, 127.0.0.1:6333, healthy, 42 legacy collections)

## 1. Goal

Collapse all RAG vector stores into **one Qdrant collection** with native hybrid
(dense ANN + sparse BM25, server-side RRF). The mature `app/rag/` stack (Qwen3-Embedding-4B, chunking, dedup, rerank) is kept and retargeted; the legacy `app/rag_service/`
stack (FAISS, multi-head crypto_embeddings, semantic cache) is retired.

## 2. Unified Collection Schema

**Collection: `rag_unified_v1`** — mirrors the already-live v2 pattern
(`threat_intel_v2`, `scanner_v2`): named vectors `dense` + sparse `bm25`.

```json
{
  "vectors":   {"dense": {"size": 2560, "distance": "Cosine"}},
  "sparse_vectors": {"bm25": {"modifier": "idf"}},
  "on_disk_payload": false,
  "optimizers_config": {"indexing_threshold": 500}
}
```

- **dense**: qwen3-embedding-4b via llama.cpp :8004 / DeepInfra (2560d, Cosine — L2-normalized on insert, consistent
  with every existing collection).
- **sparse `bm25`**: Qdrant server-side BM25 (k1=1.5, b=0.75 defaults) computed from the
  `content` payload field. **No fastembed/SPLADE dependency** — reuse the exact config
  the fleet already runs. If lexical quality regresses vs. the in-process BM25-lite
  (which has corpus IDF), evaluate fastembed `Qdrant/bm25` as a drop-in named sparse
  vector (dim-agnostic; requires `fastembed` in the app container). [REVIEW]

**Point ID — idempotent upsert.** `point_id = uuid5(URL_NAMESPACE, doc_id)` where
`doc_id = "{source}:{content_type}:{sha256(text)[:16]}"`. Upsert (`/points?wait=true`)
overwrites in place — no duplicates on re-ingest, matching `scan_memory.py`'s existing
uuid5 pattern.

**Payload schema (metadata filters):**

| Field | Type | Example | Filters on |
|---|---|---|---|
| `doc_id` | str | `news:article:ab12cd34ef56` | exact |
| `source` | str | `news_crawler`, `scan_v2`, `rekt_db` | exact |
| `content_type` | str | `news`, `scan`, `audit`, `wallet`, `pattern`, `threat` | exact |
| `content` | str | chunk text | BM25 (sparse) source |
| `chain` | str? | `solana`, `evm` | exact |
| `address` | str? | `0x…`, token mint | exact |
| `created_at` / `updated_at` | int (unix) | 1755100000 | range |
| `collection` | str | legacy provenance (`scam_intel`) | exact — back-compat queries |
| `group_id` | str? | dedup story group | exact |

Keyword index on `source`, `content_type`, `chain`, `address`, `collection`, `group_id`
(payload index → filter performance).

**Delete/update:** `DELETE /points` by stable uuid5(doc_id) (delete by source/content_type
via filter scroll first, then delete by IDs). Update = upsert same point_id. TTL-style
expiry: nightly job scrolls `updated_at` older than per-source retention, deletes.

## 3. Hybrid Search (server-side RRF)

Replace `three_pillar_search`'s in-process RRF (k=60) with Qdrant native prefetch+fusion:

```json
POST /collections/rag_unified_v1/points/search
{
  "prefetch": [
    {"query": {"name": "dense", "vector": <2560d>}, "using": "dense", "limit": 50, "filter": {...}},
    {"query": {"name": "bm25", "text": "query terms"}, "using": "bm25", "limit": 50, "filter": {...}}
  ],
  "query": {"fusion": "rrf"},
  "limit": 20,
  "with_payload": true
}
```

Keep the outer wrappers unchanged: `QueryExpander` → prefetch dense+sparse →
`HybridRetriever` fusion → local Qwen3-Reranker-4B (:8003, `rerank.py`) → `ContextBuilder`.
Metadata filters (chain, content_type, source) move into the Qdrant `filter` — kills the
in-process `_apply_filters` post-filter step.

## 4. What Retires (delete after cutover)

| Component | Replacement |
|---|---|
| `app/rag/ann_index.py` (numpy ANN + Redis `rag:ann:*` persist) | Qdrant dense |
| `engine._keyword_search` BM25-lite (Redis text scan, k1=1.5/b=0.75) | Qdrant `bm25` sparse |
| `engine._qdrant_search` + named-vector probing (`_qdrant_uses_named_vectors`) | becomes the only path |
| `app/ann_index.py` (root, FAISS) + `app/rag_service/retrieval.py` FAISS path | Qdrant (rag_service deprecated) |
| `app/semantic_cache.py` (Redis vec cache) | drop — Qdrant is the cache; keep only if latency regresses [REVIEW] |
| `app/crypto_embeddings.py` multi-head bge-small (384d) | qwen3-4b 2560d; note: file already missing, rag_service imports are dangling [REVIEW] |
| 42 legacy Qdrant collections (news_v2, scams_v2, threat_intel_v2, scanned_tokens…) | frozen → alias-read → dropped |

**Keep:** `dedup.py` Redis ZSET levels 1/3/4 (URL/fingerprint/entity — Redis is the right
tool); Level 2 title-sim re-targets `rag_unified_v1` (cosine ≥ 0.95). `CachedEmbedder`
(embedding cache) stays. `evaluation.py` (RAGAS) stays as the regression gate.

**LanceDB chain data (`lite-chain-indexer/vectors/`):** **do not move.** Directory does
not exist in this repo (chain data lives in the separate `chain-indexer` repo). LanceDB is
embedded, zero-ops, and append-heavy with exact-match (block/address) access — not ANN.
Separate concern, separate lifecycle. [REVIEW] only if chain semantic search is ever
needed.

## 5. Migration Path (re-index)

Sources: ClickHouse (news/scan/whale tables), Postgres (audits, threat intel, feedback),
and legacy Redis/FAISS dumps (rag_service patterns). All re-indexing goes through the
**same idempotent writer** (`ensure_collection` → batch embed qwen3-4b → upsert uuid5 IDs),
so re-runs are safe.

1. Export rows → chunk (`chunking.py`, existing 512-token/15% overlap) → embed (Qwen3-Embedding-4B via DeepInfra
   primary + local :8004 backup) → upsert.
2. Backfill order by content_type priority: news → threat/scan → audit → wallet/pattern.
3. Run `evaluation.py` on the new collection pre/post; gate cutover on recall parity
   (target: ≥ current hybrid, expected +5-15% from real BM25 IDF).
4. Keep legacy collections read-only (alias `rag_unified_v1` under legacy names during
   cutover window) so old queries keep working; drop when traffic drains.

## 6. Phased Order + Effort

| Phase | Work | Effort |
|---|---|---|
| 0 | Qdrant client module (`app/rag/qdrant.py`), settings, `rag_unified_v1` create + payload indexes | 0.5 d |
| 1 | Writer: idempotent upsert/delete wrapper; retarget `scan_memory.py` + dedup L2 | 1 d |
| 2 | Search: native prefetch+RRF replacing `_qdrant_search`/`_keyword_search`/numpy ANN; keep HybridRetriever/rerank wrappers | 1.5 d |
| 3 | Backfill re-index from ClickHouse/Postgres/legacy dumps (idempotent) | 1–2 d |
| 4 | Cutover feature flag; freeze legacy collections → alias → drop; delete numpy/FAISS/BM25-lite/rag_service code | 1 d |
| 5 | RAGAS regression + latency smoke (p95) + cleanup | 0.5 d |

**Total ≈ 5.5–7 engineer-days.** No downtime: Qdrant already healthy; write path is
additive; search swaps behind a flag.

## 7. [REVIEW] Flags

1. Sparse choice: server-side `bm25` (zero deps, fleet-proven) vs. fastembed bm25 if
   lexical recall regresses vs. corpus-IDF BM25-lite.
2. `app/crypto_embeddings.py` is missing but `app/rag_service/*` still imports it —
   dangling legacy imports; confirm rag_service is truly dead before deleting.
3. Semantic cache drop: verify p95 latency budget without it.
4. `scan_memory`/`whale_indexer` writers currently target per-domain collections — they
   must be retargeted in Phase 1, not Phase 4, to avoid dual-write drift.
5. Existing v2 collections already carry named dense+bm25 — confirm none are actively
   consumed by `rugbrain`/`rmi-core` external writers before dropping (cross-repo contract).
