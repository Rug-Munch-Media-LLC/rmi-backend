# RugBrain Extraction Plan — RAG → Standalone OSS Repo

**Status:** DRAFT · READ-ONLY design (no code changed) · 2026-08-13
**Owner:** RMI Engineering · **Pattern:** mirror of rugmunch-scanner extraction (port → decouple → shim → delete)
**License target:** MIT (OSS) · **Companion:** `app/rag/QDRANT-CONSOLIDATION.md`

## 1. Package name
**`rugbrain`** — matches `docs/RAG_ARCHITECTURE.md` (already canonically named RugBrain). Conflicts: an existing `rmi-rag` repo at /srv/work/repos/rmi-rag already claims the "RugBrain" name with a different codebase (`src/rmi_rag/`) — [REVIEW] decide supersede vs. merge before creating the new repo.

## 2. Module map (what moves)
**Move (mature `app/rag/` core):** `__init__`, `config`, `compat`, `engine`, `service` (RAGService), `retrieval`, `qdrant_store`, `embedder`, `embeddings`, `rerank`, `chunking`, `dedup`, `ingestion`, `ingestion_models`, `processor`, `models`, `quality`, `rate_limiter`, `ratings`, `evaluation`, `feedback`, `intelligence`, `ta_knowledge`, `tools`, `tracing`, `ann_index`, `scan_memory`, `crawler`, `_crawler_constants`, `_crawler_helpers`, `graph` (14,014 LOC rag+rag_service).

**Move (top-level RAG satellites):** `crypto_embeddings/`, `splade_bm25.py`, `cross_encoder_reranker.py`, `entity_extraction/`, `mmr_dedup.py`, `query_transform.py`, `rag_observability.py`, `confidence.py`, `semantic_cache.py`, `scam_sources.py`, `ann_index.py`, `intel_pipeline.py`, `github_rag_feeder.py`, `rag_agentic.py`, `rag_tool.py`.

**Retire (legacy, per QDRANT-CONSOLIDATION):** `app/rag_service/` (FAISS, multi-head embeddings) — do NOT port; delete after v1 unified collection is live.

**Stays in rmi-backend (integration glue):** `app/databus/rag_ingestion.py` (depends on databus providers), `rag_metrics_api.py`, API routes (`app/api/v1/rag/`).

## 3. Dependency list (must decouple)
Grep of `app.*` imports across rag/rag_service/satellites:
- **Core infra:** `app.core.logging`, `app.core.redis`, `app.core.metrics`, `app.core.health`, `app.core.internal_key`, `app.core.lifespan`, `app.config`, `app.llm_config` → replace with local adapters (logger, cache, config, metrics, LLM router).
- **DB/vector:** `app.db` (SQLAlchemy session), `app.knowledge_graph.graph`, `app.qdrant_ann` → local `db.py` + `qdrant` client adapters.
- **Domains/services:** `app.domains.scanner`, `app.services.pryscraper_client`, `app.services.web3_chat.balances`, `app.databus.{news_intel, news_provider, social_intel, x_intel}` → inject via protocol interfaces (ScannerClient, NewsProvider) rather than imports.
- **Pure deps (move cleanly):** `app.crypto_embeddings`, `app.splade_bm25`, etc. — internal to the extraction set.

## 4. Staged extraction (mirror scanner)
1. **Port** — copy modules verbatim into `rugbrain/`, import-shim `from rugbrain.*`, no behavior change; verify Qdrant reachability + collection schema per QDRANT-CONSOLIDATION.
2. **Decouple** — replace `app.core.*`/`app.db`/`app.domains.*` with local adapters; define `ScannerClient`, `NewsProvider`, `LLMRouter` protocols; settings via pydantic-settings.
3. **Shim** — rmi-backend keeps `app.rag` as thin re-export shims → API routes unchanged; add `rugbrain` as a dependency (local path → git).
4. **Delete** — migrate API routes off shims, delete `app/rag/` + satellites + `rag_service/`; point RAG_MODERNIZATION docs at new repo.

## 5. Public API (target)
`RAGService` (engine, query → cited chunks), `embedder` (bge-m3/Infinity 1024d), `rerank` (cross-encoder), `qdrant_store` (unified `rag_unified_v1` dense+bm25 hybrid, uuid5 idempotent upserts), `evaluation` (RAGAS-style eval + feedback ingestion).

## 6. Repo scaffold (new)
`rugbrain/`, `pyproject.toml`, `tests/`, `docs/`, `LICENSE` (MIT), standard fleet files (AGENTS.md, SECURITY.md, CODEOWNERS, commitlint, lefthook, gitleaks) per standards templates. NOT ported: internal hostnames, gopass paths, proprietary admin tooling.
