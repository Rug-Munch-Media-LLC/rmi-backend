# RugBrain Extraction Checklist — app/rag → standalone OSS `rugbrain` package

**Status:** CONCRETE · read/write planning (scaffold exists, no code migrated) · 2026-08-14
**Owner:** RMI Engineering · **Supersedes:** `docs/RUGBRAIN-EXTRACTION.md` (36-line draft)
**Companion:** `docs/RUGBRAIN_RUGMUNCHBOT_OSS_PLAN.md` (bot boundary), `app/rag/QDRANT-CONSOLIDATION.md`
**Target repo:** `/srv/work/repos/rmi-rag` (4 remotes: forgejo, codeberg, gitlab, radicle)

> This checklist is the **module-by-module, ordered** execution plan. The module map below was
> derived from an actual import audit of `app/rag/*.py` (2026-08-14), not from memory — every
> classification cites the exact `app.*` imports that make it pure / portable / private-coupled.

---

## 0. Ground rules (from the rename-failure lesson, plan §3.0)

1. One atomic extraction unit per commit; monorepo stays green at every commit.
2. Grep every reference (`rg -n "app\.rag|rugbrain" --type py` + configs + CI) before each move.
3. Audit lazy maps first: `__all__`, `__getattr__`, `importlib`, `TYPE_CHECKING`, string registrations.
4. Keep a thin re-export shim in `app/rag/` for one release cycle, then delete (the `compat.py` pattern).
5. Run full suite + `-m rag_eval` + `ruff` + `mypy --strict` + `gitleaks detect --no-git` per move.

---

## 1. Module map (verified by import audit)

Legend: **PURE** = only `app.rag.*` + stdlib (logger/redis swap required); **PORTABLE** = one or two
injectable seams (`app.config.settings`, lazy `app.*` import); **PRIVATE-COUPLED** = imports a private
domain/service — needs a protocol or stays behind.

### 1.1 PURE — copy verbatim, swap `app.core.logging` → local logger

| Module | LOC | app.* imports | Notes |
|---|---|---|---|
| `models.py` | 105 | — (none) | Wire contract (`SearchRequest/Response`, `IngestRequest/Result`, `FeedbackRecord`) → `rugbrain/core/models.py` |
| `config.py` | 99 | — (self-ref only) | All knobs verbatim → `rugbrain/settings.py` + `rugbrain/core/config.py` |
| `ingestion_models.py` | 59 | — | → `rugbrain/ingest/ingestion_models.py` |
| `_crawler_constants.py` | 391 | — | Public feed constants → `rugbrain/ingest/_crawler_constants.py` |
| `chunking.py` | 601 | `app.core.logging`, `app.core.redis` | Redis only for dedup-hash cache → `CacheBackend` protocol (in-memory fallback) |
| `processor.py` | 57 | `app.core.logging`, `app.rag.*` | → `rugbrain/ingest/processor.py` |
| `rate_limiter.py` | 80 | `app.core.logging`, `app.core.redis`, `app.rag.config` | Redis → `CacheBackend`; token-bucket math portable |
| `quality.py` | 99 | `app.core.logging`, `app.core.redis`, `app.rag.*` | Spam gate → in-memory default |
| `ingestion.py` | 420 | `app.core.logging`, `app.rag.*` | BatchIngestor, scheduler — Redis default via `CacheBackend` |
| `feedback.py` | 228 | `app.core.logging`, `app.core.redis` | → `rugbrain/core/feedback.py` |
| `ratings.py` | 628 | `app.core.logging`, `app.rag.dedup` | ⚠️ *not in original module list* — include (used by crawler) |
| `ta_knowledge.py` | 916 | `app.rag.engine`, self | ⚠️ *not in original module list* — include if TA docs ship; else private |
| `evaluation.py` | 723 | `app.rag.{config,models,service}` | Eval gates `RAG_EVAL_*` → `rugbrain/core/evaluation.py` |

### 1.2 PORTABLE — copy + one injectable seam

| Module | LOC | Seam to replace | Where |
|---|---|---|---|
| `embeddings.py` | 144 | `app.config` → `settings.infinity_url`; `app.rag.embedder` | `rugbrain/embed/embeddings.py` |
| `embedder.py` | 758 | `app.config` (backend selection) | `rugbrain/embed/embedder.py` |
| `rerank.py` | 102 | `app.config` (Infinity `/rerank`); `app.rag.tracing` | `rugbrain/core/rerank.py` |
| `engine.py` | 906 | `app.config.settings` read **at import time** (line 54); `app.core.redis` lazy | `rugbrain/core/engine.py` — move settings to lazy resolution |
| `qdrant_store.py` | 497 | `app.config.settings` (env fallback already present); lazy `engine._build_sparse_query` | `rugbrain/storage/qdrant_store.py` |
| `retrieval.py` | 781 | Lazy `app.knowledge_graph.graph` (line 553) → optional `KGProvider` | `rugbrain/core/retrieval.py` |
| `crawler.py` | 549 | `app.config.settings` (lines 39/372/384); `app.rag.*` | `rugbrain/ingest/crawler.py` |
| `_crawler_helpers.py` | 527 | `app.core.logging` | `rugbrain/ingest/_crawler_helpers.py` |
| `ann_index.py` | 298 | `app.core.redis`, `app.core.logging`, `app.rag.embeddings` | `rugbrain/storage/ann_index.py` |
| `dedup.py` | 819 | `app.core.redis`; lazy `engine._qdrant_search` (line 414) | `rugbrain/core/dedup.py` |
| `service.py` | 193 | `app.core.{lifespan,metrics,redis,logging}` | `rugbrain/core/service.py` — `RAGService` facade |
| `tracing.py` | 144 | `app.core.logging`; **`_DEFAULT_ENDPOINT = "http://phoenix:4317"`** (internal docker hostname) → env, off by default | `rugbrain/observability/tracing.py` |

### 1.3 PRIVATE-COUPLED — protocol seam or stays private

| Module | LOC | Private imports | Protocol needed | OSS treatment |
|---|---|---|---|---|
| `tools.py` | 243 | `app.domains.scanner.scan_token`, `app.services.web3_chat.balances.get_wallet_balance` | `ScannerClient`, `BalancesClient` | Ship `tools_oss.py` demo with stubs, or drop tools from v1 |
| `graph.py` | 461 | `app.llm_config.generate_content`; `app.rag.tools` | `KGProvider` + `LLMRouter` | Optional `KGProvider` (default `None`); LLM seam injectable |
| `pryscraper_ingest.py` | 101 | `app.services.pryscraper_client.PryscraperClient` | `CrawlerProvider` | Stub no-op provider behind `CrawlerProvider` |
| `intelligence.py` | 505 | `app.rag.{retrieval,rerank}` only — *imports are portable*; semantics are private (scanner-result enrichment) | — (portable imports) | **Keep private in v1** per plan; optional `RAGIntelligenceEnricher` behind neutral payload schema |
| `scan_memory.py` | 352 | `app.config`, `app.rag.embeddings` — *imports portable*; `scan_v1` payload contract is private | `ScanMemoryProvider` (optional) | Keep private in v1; neutral `scan_*` schema if shipped |
| `compat.py` | 336 | `app.crypto_embeddings` | — | **LEAVE BEHIND**; ship minimal `compat.py` only if dict-shaped API needed |
| `ingest_crypto_knowledge.py` | 1308 | **`app.core.internal_key.INTERNAL_API_KEY` + `X-RMI-Key` header** (lines 50/73) | — | **Never ships** — one-shot private ingest script; contains shared-secret auth |

> ⚠️ **Discrepancies vs. plan doc** (verified by import audit): `intelligence.py` and `scan_memory.py`
> do **not** import `app.crypto_embeddings` or private domains directly — their imports are portable.
> They are flagged private because of *data contracts* (`scan_v1`, scanner-result semantics), not imports.
> `ingest_crypto_knowledge.py` was not listed in the plan but is the **highest-severity leak** (`X-RMI-Key`).

---

## 2. Boundary protocols (defined in `rugbrain/providers.py`)

```python
# rugbrain/src/rugbrain/providers.py — all protocols are runtime-checkable
class EmbeddingProvider(Protocol):      # embed/embeddings.py
    async def embed(self, texts: list[str], *, collection: str = "") -> list[list[float]]: ...

class Reranker(Protocol):               # core/rerank.py
    async def rerank(self, query: str, docs: list[dict], top_k: int = 5) -> list[dict]: ...

class CacheBackend(Protocol):           # storage/cache.py (replaces app.core.redis)
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int = 0) -> None: ...
    async def incr(self, key: str, amount: int = 1) -> int: ...

class ScannerClient(Protocol):          # replaces app.domains.scanner.scan_token
    async def scan_token(self, query: str, chain: str = "ethereum") -> dict[str, Any]: ...

class BalancesClient(Protocol):         # replaces app.services.web3_chat.balances.get_wallet_balance
    async def get_wallet_balance(self, query: str, chain: str | None = None) -> dict[str, Any]: ...

class KGProvider(Protocol):             # replaces lazy app.knowledge_graph.graph (retrieval.py:553)
    async def entity_boost(self, query: str, tokens: list[str]) -> dict[str, float]: ...

class CrawlerProvider(Protocol):        # replaces app.services.pryscraper_client (pryscraper_ingest.py)
    async def extract(self, url: str) -> str: ...

class NewsProvider(Protocol):           # optional news tool source
    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...

class LLMRouter(Protocol):              # replaces app.llm_config.generate_content (graph.py)
    async def generate(self, prompt: str, **kw: Any) -> str: ...
```

Shipped stubs: `stubs/scanner_client_stub.py`, `stubs/balances_client_stub.py`,
`stubs/crawler_provider_stub.py` (no-op extract), `stubs/news_provider_stub.py` (bundled sample docs).

---

## 3. Ordered extraction steps (P0)

### Step 0 — Repo decision (⚠️ [REVIEW], blocks everything)
`/srv/work/repos/rmi-rag` already ships `pyproject.toml name="rugbrain"` for a **different** codebase
(`src/rmi_rag/`, duckdb/fastembed/pyarrow stack) plus a `src/rugbrain/__init__.py` that aliases
`sys.modules` to `rmi_rag`. The scaffold in `rugbrain/` (this task) is the extraction target.
**Decision needed:** supersede `rmi_rag` (rename to `_legacy/`, it already has `src/rmi_rag/_legacy/`),
or merge, or rename the new package. Do NOT overwrite the root `pyproject.toml` until decided.

### Step 1 — Scaffold (DONE by this task, `rugbrain/` dir)
- [x] `pyproject.toml` name="rugbrain", src layout, `rugbrain` console script
- [x] `src/rugbrain/__init__.py` public API re-exports (stub `__getattr__` → NotImplementedError w/ source map)
- [x] `providers.py` protocols, `settings.py` (pydantic-settings, env-driven, **no secrets**)
- [x] `config/collections.example.json` (neutral schema) + `config/crawl_sources.example.json` (public groups)
- [x] README.md (self-hosting, no internal references), MIGRATION.md
- [x] Stub modules per §1.1/§1.2 with `TODO(rugbrain-extract)` pointing at `app/rag/<module>.py`

### Step 2 — Pure core (one commit per module, §0 rules)
- [ ] Port `models.py`, `config.py` → `rugbrain/core/` (+ `core/__init__.py` re-exports)
- [ ] Port `chunking.py`, `dedup.py`, `quality.py`, `processor.py`, `rate_limiter.py`, `ingestion.py`, `ingestion_models.py` → `rugbrain/ingest/`; introduce `CacheBackend` (in-memory default) in place of `app.core.redis`
- [ ] Port `evaluation.py`, `feedback.py`, `ratings.py` → `rugbrain/core/`; keep `RAG_EVAL_*` gates
- [ ] Port `_crawler_constants.py`, `_crawler_helpers.py` → `rugbrain/ingest/`; **scrub `incident_sources` per §4** (verified: all URLs currently public — keep policy scrub)

### Step 3 — Portable core (seams)
- [ ] Create `rugbrain/settings.py` (pydantic-settings, env table per plan §1.5); replace `app.config.settings` in `engine.py` (fix import-time read at line 54 → lazy), `qdrant_store.py`, `crawler.py`, `embeddings.py`, `embedder.py`, `rerank.py`
- [ ] Port `engine.py` → `rugbrain/core/engine.py` (lazy settings, `CacheBackend`)
- [ ] Port `qdrant_store.py`, `ann_index.py` → `rugbrain/storage/` (lazy `engine._build_sparse_query` stays lazy)
- [ ] Port `retrieval.py` → `rugbrain/core/retrieval.py`; wrap line 553 lazy `app.knowledge_graph.graph` behind optional `KGProvider` (default `None`, fail-open)
- [ ] Port `service.py` → `rugbrain/core/service.py`; metrics → no-op adapter, lifespan → optional hook
- [ ] Port `tracing.py` → `rugbrain/observability/tracing.py`; **replace `http://phoenix:4317` default with env-only, off by default**

### Step 4 — Private-coupled (protocol seams or exclude)
- [ ] `providers.py` + `stubs/` shipped with the package (Step 1)
- [ ] `tools.py` → port as `rugbrain/tools_oss.py` demo with `ScannerClientStub`/`BalancesClientStub`, or defer to P1
- [ ] `graph.py` → port only if `KGProvider` + `LLMRouter` seams accepted; else defer
- [ ] `pryscraper_ingest.py` → `rugbrain/ingest/pryscraper_ingest.py` behind `CrawlerProvider` (no-op default)
- [ ] `intelligence.py`, `scan_memory.py`, `ingest_crypto_knowledge.py`, `compat.py`, `ta_knowledge.py` → **stay private** (monorepo only)

### Step 5 — Tests, CI, shims
- [ ] Port `tests/test_rag_*.py`, chunking/dedup/qdrant_store tests → `rugbrain/tests/` (in-memory Qdrant + respx-mocked Infinity)
- [ ] Shim `app/rag/*` re-exports → `import rugbrain` aliases for one release
- [ ] `rugbrain seed-demo` CLI + `examples/data/*.md` (fake audit report, fake post-mortem)
- [ ] Fleet files: AGENTS.md, LICENSE (MIT), SECURITY.md, CODEOWNERS, .gitleaks.toml, lefthook.yml, commitlint
- [ ] Tag `v0.1.0`, add to `push-all-repos.sh`, fleet-compliance gates

---

## 4. Internal-reference scrub list (pre-public-push gate)

| # | Severity | Location | Reference | Fix |
|---|---|---|---|---|
| 1 | 🔴 CRITICAL | `app/rag/ingest_crypto_knowledge.py:50,73` | `INTERNAL_API_KEY` + `X-RMI-Key` shared-secret header | Never port this file; add gitleaks rule for `X-RMI-Key` |
| 2 | 🔴 HIGH | `app/rag/tracing.py:46` | `_DEFAULT_ENDPOINT = "http://phoenix:4317"` (internal docker hostname) | env-only default, off by default |
| 3 | 🟡 MED | `app/rag/tracing.py:46,129` | `_DEFAULT_PROJECT = "rugmunch-rag"` (brand) | rename to `rugbrain` |
| 4 | 🟡 MED | `app/rag/graph.py:56,322` | "RugMunchScan v2 (SENTINEL)", "crypto intelligence analyst for Rug Munch Media" | neutral prompt text |
| 5 | 🟡 MED | `app/rag/tools.py:3,15,177,199` | "RugMunch data", "RugMunchScan v2 (SENTINEL)" | neutral names |
| 6 | 🟡 MED | `app/rag/scan_memory.py:3` | "RugMunchBot token scan" docstring | neutral wording |
| 7 | 🟡 MED | `app/rag/models.py` `COLLECTIONS` | `scam_intel`, `deployer_history`, `wallet_labels`, `contract_audit`, `phishing_db`, `scanned_tokens`, `stablecoin_market` | private data contracts → neutral names in `collections.example.json` |
| 8 | 🟡 MED | `app/rag/config.py` `RAG_COLLECTIONS` | `scanner`, `wallet`, `news`, `research`, `scams` | neutral defaults (`docs`, `news`, `research`) |
| 9 | 🟢 LOW | `app/rag/crawl_sources.json` `incident_sources` | Verified 2026-08-14: **all URLs public** (DefiLlama, REKT, SlowMist, CertiK, MetaMask, DeFiHackLabs, ethereum-labels, ConsenSys, crytic, kadenzipfel, OpenZeppelin) | Keep policy scrub + move to `RAG_CRAWL_SOURCES_EXTRA` env |
| 10 | 🟢 LOW | all modules | `app.core.{logging,redis,metrics,health,lifespan,internal_key}`, `app.config.settings` (7 modules), `app.llm_config`, `app.knowledge_graph.graph`, `app.crypto_embeddings`, `app.domains.scanner`, `app.services.*` | local adapters / protocols per §1–§2 |

**Gate:** run `gitleaks detect --no-git` + `rg -n "rugmunch\.io|gopass|INTERNAL|phoenix:|X-RMI-Key" rugbrain/` before first public tag.

---

## 5. Open [REVIEW] flags

| # | Flag |
|---|---|
| R1 | Repo collision: existing `rmi-rag` `src/rugbrain` is a `sys.modules` alias to `rmi_rag` — supersede/merge/rename decision blocks pyproject root overwrite |
| R2 | `ta_knowledge.py` (916 LOC) & `ratings.py` (628 LOC) missing from original module list — confirm ship-vs-private |
| R3 | `intelligence.py`/`scan_memory.py` flagged private by data contract, not imports — reconsider OSS-neutral schemas for v1.1 |
| R4 | `tracing.py` phoenix endpoint — confirm no other docker-compose service hostnames leak (`rg "http://[a-z]+:[0-9]+" app/rag/`) |
