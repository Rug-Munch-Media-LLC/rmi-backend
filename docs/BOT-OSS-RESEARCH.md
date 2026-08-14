# RugMunchBot — OSS Landscape Research (2026-08)

**Status:** research-only findings · **Author:** AI research pass · **Scope:** open-source software that could materially improve RugMunchBot's chat/RAG/tools without reinventing · **Commit:** none (this is a findings doc only)

> `[REVIEW]` marks candidates whose maturity/claims I could not fully verify at research time — validate before adopting.
> "Verdict" vocabulary: **adopt** = do it, **consider** = strong conditional fit, **skip** = don't (or only as last resort).

---

## 0.0 Top-5 ranked recommendations (TL;DR)

1. **Pydantic AI** (`pydantic-ai`, MIT) — typed tool-calling agent for the deep path, replacing regex-only routing (P1.1). Async-native, LiteLLM/OpenRouter/Ollama support matches our Zen+Qwen stack, folds structured output + retry + OTel into one layer. Keep regex as fast path. Effort S–M.
2. **Qdrant hybrid retrieval (dense + sparse BM25 + RRF) + MMR + contextual-retrieval chunk enrichment** — zero new OSS, highest-ROI retrieval upgrade; the "better RAG" answer is mostly technique, not new software (P0.3). Effort M.
3. **Arize Phoenix** (`arize-phoenix`, Apache-2.0 + ELv2 carve-outs) — tracing + LLM evals; we already emit OpenInference spans, so it's a Docker container + OTLP endpoint. Effort S.
4. **RAGAS** (`ragas`, Apache-2.0) — golden-set eval harness (faithfulness/answer relevance/context precision) feeding P1.5's feedback loop; accept maintenance stall or swap to DeepEval (active). Effort S.
5. **Reranker + embedder upgrade (gte-multilingual-reranker-base now; Qwen3-Reranker-0.6B + Qwen3-Embedding-0.6B via TEI/vLLM when ready)** — gte-multilingual-reranker-base is a zero-ops Infinity drop-in (S); Qwen3 models are the bigger quality jump (+8.8 MTEB-R for the reranker) but need a TEI/vLLM container, and the embedder swap only when re-embedding is scheduled. All Apache-2.0; jina models are non-commercial, skip.

Honorable mentions: **LightRAG** (graph RAG on our existing Neo4j+Qdrant for entity-linked questions), **json_repair** (1-line safety net, adopt regardless), **CryptoBERT via Infinity `classify`** (cheap sentiment), **DefiLlama python-sdk + api-docs corpus**, **Subsquid/Envio-Hypersync clients** (historical on-chain backfill).

---

## 0. Current stack snapshot (what we're improving)

| Layer | Today |
|-------|-------|
| Chat pipeline | `handlers/chat_ai/chat_ai_response` → bridge context (`chat-rag-bridge.py`) → Zen (DeepSeek V4 Flash Free via LiteLLM) → fallback Qwen3-4B local |
| Intent routing | **Regex/keyword** `route_tools()` in `tools/intent.py` (deliberate: cheap, deterministic, no LLM round-trip) |
| Tool registry | Hand-rolled `ToolRegistry` (`tools/registry.py`): ~15 tools (price, market, security, onchain, ta, trending, web, rag_lookup, …), timeout 6s, TTL 60s, fail-open |
| Embeddings | `BAAI/bge-m3` 1024d via Infinity (`app/rag/embedder.py`) |
| Rerank | `BAAI/bge-reranker-v2-m3` via Infinity `/rerank` (`app/rag/rerank.py`) — **broken upstream, `[REVIEW]` hang noted in BOT-IMPROVEMENT-PLAN P0.3** |
| Vector store | Qdrant (modernization target) — historical Redis-ANN legacy; hybrid dense+BM25 RRF is planned but not fully shipped |
| Observability | OpenInference-style spans already emitted in RAG path (`app/rag/tracing.py`) |
| Infra | Talos Docker host, Postgres / ClickHouse / Neo4j / Dragonfly |
| Constraint | Frugality: <$3/mo marginal. Prefer self-host/free. MCP fleet at cap (no new MCP servers without pruning). |

Reference gaps this research directly feeds: `docs/BOT-IMPROVEMENT-PLAN.md` P0.3 (embedder unify + hybrid + rerank fix), P1.1 (LLM tool-use agent), P1.5 (RAGAS eval + chat feedback), P2.3 (multi-model A/B).

---

## 1. Better embeddings & rerankers (beyond bge-m3 / bge-reranker-v2-m3)

### 1.1 Embedding models

| Candidate | License | Dims | Multi-lingual | Why it helps us | Verdict |
|---|---|---|---|---|---|
| **Qwen3-Embedding-0.6B / 4B / 8B** (Alibaba) | Apache-2.0 (model card confirmed) | 1024 / 2560 / 4096 (MRL 32→max) | 100+ languages | Clear MTEB leader for open models 2025–26; 0.6B is a near-drop-in for bge-m3 (same 1024d, MTEB-ML 64.33 vs 59.56). ⚠️ **decoder-based — not on Infinity's CI-validated list** (TEI/vLLM support it; may run on Infinity's torch/ST engine but validate first) | **adopt (0.6B)** · effort **M** (model swap + serving check + corpus re-embed; see §1.3) — only when re-embedding is already on the table |
| **Fin-ModernBERT-RAG-embed-base** (sujet-ai) | Apache-2.0 `[REVIEW]` | Finance-domain-tuned ModernBERT embed (RAG-tuned); niche fit for finance-heavy chunks | consider (tiny adoption — ~130 downloads; validate against a finance eval set before trusting) |
| **FinLang/finance-embeddings-investopedia** / `iioos/crypto-embedding-model` | `[REVIEW]` license | Finance/crypto-domain embeddings (16.9k / new Dec 2025); **no benchmarks, unestablished** | skip (general multilingual beats niche embedders for RAG) |
| **ModernBERT-embed-base / embed-large** (answer.ai / lightonai) | Apache-2.0 | 768 / 1024 | **English-centric** | Fast 8192-ctx encoder, strong English retrieval; not multilingual — weaker for our mixed-language TG traffic | skip (English-centric) |
| **nomic-embed-text-v1.5 / v2-moe** | Apache-2.0 | 768 | v1.5 English; v2-moe multilingual | v1.5: 8192 ctx, Infinity-tested; **v2-moe max 512 tokens** — too short for long-doc RAG | skip (legacy direction; v2's 512-ctx is a trap) |
| **intfloat/multilingual-e5-large-instruct** | MIT | 1024 | 100+ | Instruction-tuned retrieval ("query: …" prefix); **512-token truncation limit**; Infinity-tested | skip (bge-m3 ≈ same class) |
| **gte-multilingual-base / gte-Qwen2** (Alibaba) | Apache-2.0 | 768 / 1536 / 3584 | 70+ | gte-multilingual-base: 8192 ctx, dense+sparse; gte-Qwen2-7B is Infinity-tested | skip (same generation as bge-m3) |
| **mxbai-embed-large-v1** | Apache-2.0 | 1024 | English only | Good English retrieval; Infinity-tested; no multilingual | skip |
| **Snowflake arctic-embed-v2 / multilingual** | Apache-2.0 | 1024 (matryoshka) | m-v2.0 ~70 langs | Long-context, fast encoder; below Qwen3-0.6B on multilingual MTEB | skip |
| **jina-embeddings-v3** | **CC-BY-NC-4.0** (non-commercial) | 1024 | 89 | Task-token prompting; **commercial use requires paid Jina API** | skip (license blocker for a commercial bot) |

**Recommendation:** standardize on **Qwen3-Embedding-0.6B** when we do the P0.3 re-embed (or stay on bge-m3 if re-embedding cost is the blocker — bge-m3 is still a fine baseline; the win is one embedder + hybrid, not the model swap itself). Plan a **TEI/vLLM serving sidecar** (or validate Qwen3 on Infinity's torch engine first `[REVIEW]`) since Qwen3 is decoder-based and not on Infinity's validated list. If we never re-embed: keep bge-m3, spend the budget on retrieval quality (§2).

### 1.2 Reranker models

| Candidate | License | Why it helps | Verdict |
|---|---|---|---|
| **Qwen3-Reranker-0.6B / 4B** | Apache-2.0 | Biggest open reranker jump: MTEB-R 65.80 (0.6B) / 69.76 (4B) vs bge-reranker-v2-m3's 57.03. ⚠️ **causal-LM logit scorer — will NOT run through Infinity's rerank endpoint** (BERT-style classifiers only); serve via **TEI or vLLM** (or FlagEmbedding). 4B ≈ 8GB fp16; 0.6B fits CPU | **adopt (0.6B)** · effort **M** (one extra inference container; worth it for +8.8 MTEB-R pts) |
| **gte-multilingual-reranker-base** (Alibaba) | Apache-2.0 | Modest but real gain (MTEB-R 59.51 vs 57.03), 306M, 8192 ctx, 70+ langs, **Infinity-tested** — the zero-ops, license-clean drop-in | **adopt** · effort **S** (immediate swap on existing Infinity; do this first, Qwen3 later) |
| **bge-reranker-v2.5-gemma2-lightweight** (BAAI) | **Gemma ToU** (not MIT; prohibited-use clause) | Strong BEIR scores (63.67 vs 55.36) but 9B-based, heavy VRAM, custom `trust_remote_code` loader | skip (license friction + weight) |
| **jina-reranker-v2** | **CC-BY-NC-4.0** (non-commercial) | Perf beats v2-m3 on multilingual benchmarks, 278M, 1024 ctx | skip (license blocker for a commercial bot) |
| **FlagEmbedding (BAAI) pkg** (`pip install FlagEmbedding`, MIT) | MIT | Canonical local runner for bge/bge-m3 rerankers incl. the gemma2-lightweight variant; **not** the runner for Qwen3 models (those use sentence-transformers/TEI/vLLM) | keep as fallback knowledge |

**Latency note:** cross-encoder rerank on CPU is ~1–2s for top-20 — LightRAG docs say the same. Rerank top-10→3 instead of 20→5 if the 6s tool budget binds. Fix the upstream rerank hang (BOT-IMPROVEMENT-PLAN P0.3) regardless of model.

### 1.3 Embedding-change blast radius (honest cost)

- Changing embedder ⇒ **full corpus re-embed** + Qdrant collection dim change (1024→1024 for Qwen3-0.6B, so dims are compatible, but vectors differ) + re-verify collections `wallet_profiles`, `token_analysis`, `contract_audits`, `forensic_reports`, `market_intel`.
- **Serving constraint:** Qwen3-Embedding (decoder) and Qwen3-Reranker (causal-LM scorer) are **not served by Infinity's validated paths** — Qwen3 embedding needs TEI/vLLM (or Infinity-torch validation); Qwen3 reranking needs TEI/vLLM, period. gte-multilingual-reranker-base avoids this entirely.
- Reranker swap is cheap (query-time only) — do that first, independently. Recommended order: gte-multilingual-reranker-base on Infinity (S) → optionally Qwen3-Reranker-0.6B via TEI (M).
- Sequence per BOT-IMPROVEMENT-PLAN P0.3: unify embedder → store → eval gate.

---

## 2. RAG frameworks & retrieval techniques

### 2.1 Graph RAG (we already run Neo4j — this is the big unlock)

| Candidate | License | Why it helps | Effort | Verdict |
|---|---|---|---|---|
| **LightRAG** (`pip install lightrag-hku`, MIT) | MIT | Dual-layer KG+vector RAG; **Qdrant + Neo4j supported as storage backends**; local/global/hybrid/mix query modes; incremental updates + selective deletion; reranker + RAGAS + Langfuse integrations; 38.9k★, very active (2026.07 releases). Perfect for cross-document entity linking: "which exploits touched this token/address?" multi-hop questions our flat chunk RAG can't answer | M (new index alongside existing Qdrant collections; lightrag-server as Docker service) | **consider→adopt for entity-linking layer** (wallet/exploit/entity questions, B3 gap). Start in `mix`/`hybrid` mode against a curated corpus (Rekt DB, Chainabuse, SlowMist, audit reports) before touching the main KB |
| **Microsoft GraphRAG** | MIT | Original graph RAG; community-summary global queries are powerful but **LLM-index-time cost is brutal** (fine for batch, not for a chatty ingestion) | L | skip (LightRAG delivers similar value at a fraction of index cost) |
| **nano-graphrag** | MIT | Minimal single-file GraphRAG-style implementation, great for learning | S | consider (if we want to embed graph RAG into our own pipeline instead of a service) |
| **RAGFlow** | Apache-2.0 | Full RAG platform (parsing, chunking, agentic workflows, UI) | L | skip (we already have a hand-rolled pipeline + Qdrant; platform would be a rewrite, violates "enhance not duplicate") |

### 2.2 Retrieval techniques (zero new OSS — using what we have)

- **Hybrid dense + sparse (BM25) + RRF fusion in Qdrant** — Qdrant supports sparse vectors natively; RRF needs no tuning. Highest-ROI retrieval upgrade, P0.3 planned. **adopt.**
- **Contextual retrieval (Anthropic technique)** — enrich each chunk with a model-generated one-two-line "context" sentence before embedding. Cheap (batch LLM job), measurably cuts retrieval misses on terse chunks. **adopt** for ingestion-time only (no latency cost).
- **Late chunking (jina)** — embed at sentence level, chunk at doc level, no truncation loss. **consider** for long audit/forensic reports.
- **Query transformation** — HyDE / multi-query expansion before hitting Qdrant for `deep` questions (A4 gap). **consider** on the `deep` path only (latency budget).
- **MMR dedup** — Qdrant supports MMR; reduces redundant chunks in the LLM context. **adopt** (cheap).

### 2.3 Rerank/retrieval libraries

- **`rerankers` (bclavie)** — MIT, unified interface over many rerankers incl. cross-encoders & LLM rerankers; useful if we move rerank logic into our own service. **consider.**
- **`FlagRanker`** — BAAI's reranker wrapper, MIT. **consider** (paired with FlagEmbedding fallback).
- **Qdrant's own `fastembed`** (Apache-2.0) — ONNX local embeddings, could simplify embedding serving, but we already run Infinity. **skip.**

### 2.4 Evaluation harnesses (detail in §5) — RAGAS / DeepEval / TruLens

RAGAS is the standout; see §5.

---

## 3. Agent / tool-calling orchestration (replace or upgrade the regex router)

The regex router (`tools/intent.py`) is a deliberate, good default — cheap and deterministic. The question is whether a **small framework** buys us better tool *selection* for the same latency. Findings:

| Candidate | License | Why it helps | Effort | Verdict |
|---|---|---|---|---|
| **Pydantic AI** (`pip install pydantic-ai`, MIT) | MIT | Typed agent framework; **model-agnostic incl. LiteLLM/OpenRouter/Ollama/DeepSeek** (fits our Zen + Qwen fallback); Pydantic-validated **structured output** (guaranteed schema, retry-on-fail); native tool registry with dependency injection + docstring-derived schemas; async-native (fits python-telegram-bot v21); streaming; graph support for state machines; pydantic-evals; MCP support; OTel observability; 19.3k★, very active (**v2.0 shipped Jun 2026, near-daily releases — pin a version**). Direct fit for **P1.1** (LLM tool schema over regex) *without* dragging in LangChain. Note: even `instructor`'s maintainers now point users at Pydantic AI for agents | S–M (wrap existing tool handlers as `@agent.tool`; keep the registry as the executor layer) | **adopt** — the single highest-leverage upgrade. Path: keep regex router as **fast-path default** (still 90% of queries, 0 latency), use Pydantic AI as the **fallback/deep path** when regex confidence is low or query is complex. Hybrid, not rip-and-replace |
| **LangGraph** (`langgraph`, MIT) | MIT | Full state-machine graph orchestration; powerful but adds a persistence/checkpointing model we don't need for a stateless 1–2-hop TG answer | L | skip for now (overkill); revisit if we build multi-step workflows (scan→trace→report chains) |
| **OpenAI Agents SDK** (`openai-agents`, MIT) | MIT | Clean agent/guardrails/handoffs design; **now provider-agnostic (100+ LLMs via LiteLLM optional dep)**; but tracing defaults to OpenAI cloud and voice/sandbox features are OpenAI-centric | M | skip (Pydantic AI covers the same ground, better fit for our Zen/LiteLLM stack) |
| **smolagents** (`smolagents`, Apache-2.0) | Apache-2.0 | HF's minimal agent; code-as-action style (model writes Python) — powerful but **`LocalPythonExecutor` is explicitly not a security boundary**; public TG bot would need a sandbox | M | skip (typed tool-calling JSON, not code, is the right shape for a public TG bot) |
| **atomic-agents** (Eigenwise) | MIT | Lightweight Pydantic-native agent lib (6.2k★, active; forked from maximhq); minimal alternative if Pydantic AI feels heavy | S | consider (only if Pydantic AI proves too much; OpenAI-centric `[REVIEW]` provider coverage) |
| **LlamaIndex agent/workflows** | MIT | We already have LlamaIndex adapters in `app/rag/llama_adapter.py`; its agent layer is competent but heavier than needed for tool selection | M | skip (keep LlamaIndex only for what it already does) |
| **Stay hand-rolled** | — | Zero deps; the registry + timeout + TTL design is solid; upgrade = add an LLM "tool selector" prompt round (1 cheap call) that returns JSON `{tools: [...], args: {...}}` and let `ToolRegistry.execute` run it | S | **fallback plan** if we want no new dependency: hand-rolled LLM tool-selection + `json_repair` (§4). Slightly worse ergonomics, same result |

**Verdict (the §3 question, answered):** **adopt a small framework — Pydantic AI specifically** — for the *deep* path while keeping the regex router as the fast path. Hand-rolled-with-LLM-selector is the acceptable no-new-deps fallback. Do **not** adopt a heavy multi-agent framework (LangGraph/smolagents/LlamaIndex-agent) — our bot is single-conversation, 1–2 tool hops, cost/latency-bound.

---

## 4. Structured output / JSON-mode for reliable tool calls

| Candidate | License | Why it helps | Verdict |
|---|---|---|---|
| **`instructor`** (jxnl → 567-labs) | MIT | Post-processes any OpenAI-compatible response into a Pydantic schema with retry/validation; works with LiteLLM/OpenRouter; async; **maintainers now point agent use-cases at Pydantic AI** — use it only for the stay-hand-rolled path | **consider** (if we stay hand-rolled; pairs with the JSON tool-selector fallback) |
| **`json_repair`** (`pip install json-repair`, import `json_repair`) | MIT | Repairs malformed JSON from models (trailing commas, truncation); now with **schema-guided repair (Pydantic v2, beta)** + streaming; a 1-line safety net for every tool-args parse; nearly zero cost | **adopt** (add to `ToolRegistry` arg parsing regardless of framework choice) |
| **`outlines`** (dottxt-ai) | Apache-2.0 | Constrained/guided decoding — forces schema-valid output at generation time; **token-level constraints only work on local/served backends (transformers, llama.cpp, vLLM, Ollama, SGLang, TGI)**; remote OpenAI/OpenRouter gets prompt/JSON-mode only | skip for hosted Zen; **consider** for local Qwen fallback via Ollama/vLLM |
| **`guidance`** (guidance-ai) | MIT | Older constrained-decoding lib; alive but stalled (last push May 2026); local backends only | skip |
| **Server-side guided decoding** (vLLM / llama.cpp grammars / Ollama `format=json`) | MIT/varies | Zero client cost; guaranteed-valid JSON from local Qwen fallback; not available on hosted Zen | **consider** for the local-fallback path (Ollama already supports `format=json`) |
| **Pydantic AI `output_type`** | MIT | Already included if we adopt Pydantic AI (§3) — validated structured output with auto-retry | adopt (bundled) |

**Recommendation:** `json_repair` now (tiny win, everywhere); `instructor` if the hand-rolled path wins; `outlines`/server-side guided decoding only for local model serving.

---

## 5. Evaluation & observability (LLM-as-judge, tracing)

We already emit **OpenInference spans** — that makes Arize Phoenix the natural tracing home (zero protocol change).

| Candidate | License | Why it helps | Effort | Verdict |
|---|---|---|---|---|
| **Arize Phoenix** (self-host) | Apache-2.0 + **ELv2 enterprise dirs** `[REVIEW]` (verify carve-outs) | **OpenInference-native** tracing + LLM-as-judge evals; reference backend for the OpenInference spans we already emit; Docker one-liner on Talos; auto-instrumentation for LiteLLM/OpenRouter/pydantic-ai/instructor | S (docker service + OTLP endpoint) | **adopt** for tracing + online evals |
| **RAGAS** (`pip install ragas`) | Apache-2.0 | The RAG eval standard: faithfulness, answer relevance, context precision/recall; **test-set generation**; supports OpenAI-compatible judges (works with our Zen/LiteLLM). ⚠️ **activity stalled: last release Feb 2026 (~6 mo quiet), 555 open issues, org renamed vibrantlabsai** — fine as an offline harness, budget for thin maintenance. Direct fit for **P1.5** golden-set run (50 queries exist) | S (offline eval script; no runtime dep) | **adopt** (run the 50-query golden set monthly + before/after each P0/P1 change); keep **DeepEval** as the actively-maintained alternative |
| **DeepEval** (`deepeval`) | Apache-2.0 | LLM-as-judge (G-Eval etc.) with **pytest-native** integration + CI gating; 17.6k★, active | S | consider (choose over RAGAS if maintenance matters more than RAG-specific retrieval metrics) |
| **Comet Opik** | Apache-2.0 | OTel tracing + evals, self-host; polished but a second observability system we don't need | M | skip |
| **Promptfoo** | MIT | CLI eval harness (Node/npm, dev-time only), YAML-defined, LLM-as-judge, any provider incl. OpenRouter, CI-friendly | S | consider (CI-only prompt/tool-call regression harness; Node dep in a Python repo is the only friction) |
| **pydantic-evals** | MIT | Comes with Pydantic AI; simple `eval` decorators for agent evals | S | adopt (bundled with Pydantic AI if adopted) |

**Recommendation:** Phoenix (tracing, we already emit the format; verify ELv2 carve-outs) + **RAGAS** (metrics on the golden set, wired into the P1.5 feedback loop: 👍/👎 → boost/penalty; accept the maintenance-stall risk, or use **DeepEval** if we'd rather bet on an active project). Add Promptfoo only if prompt A/B becomes a recurring task.

---

## 6. Crypto/DeFi-specific OSS we could ingest or integrate

| Candidate | License | Self-host? | Why it helps | Verdict |
|---|---|---|---|---|
| **DefiLlama API + `python-sdk`** (`pip install defillama` `[REVIEW]` pkg name) | MIT (SDK) / API free no-key | No (API) | We already call it; the SDK replaces loose HTTP calls; **`DefiLlama/api-docs` (MIT) is an excellent RAG corpus** for protocol/TVL/yield questions | **adopt** (SDK + ingest api-docs into Qdrant). Note: `defillama.com/datasets` 404s `[REVIEW]` — no official open dumps; `defillama-sdk` (adapter kit) is AGPL-3.0, avoid |
| **Ponder** | MIT | Yes (we already run it) | Open EVM indexer we have in fleet; extend to index more chains/contracts for bot queries instead of adding new tools | **adopt** (enhance existing, per pushback rule #5) |
| **Subsquid (squid-sdk)** | Apache-2.0 | SDK yes; archives via SQD Network (free tier, keyless) | TS ETL → Postgres/ClickHouse for protocol event data; Apache-2.0, active | consider (S–M; pairs with our ClickHouse) |
| **Goldsky Hypersync** (now under **Envio**, `enviodev`) | Clients MPL-2.0; HyperIndex license **unclear** | Engine is SaaS; clients OSS | Ultra-fast indexed EVM/Solana queries (swaps, holders, balances) → instant chat answers | **consider** (use MPL-2.0 *clients* against hosted engine; **do not vendor HyperIndex** until license reviewed) |
| **The Graph (graph-node)** | Apache-2.0 | Yes (heavy: Postgres + IPFS + archive RPC) | Query prebuilt subgraphs via GraphQL; heavy for a VPS | skip (infra cost; Hypersync/Ponder cover) |
| **Trueblocks (chifra)** | **GPL-3.0** | Yes (needs full Erigon node) | Address-centric local Ethereum index; deep forensic lookups without RPC | skip (GPL copyleft + disk-heavy node; only revisit for a forensic product line) |
| **Ethereum ETL** (`ethereum-etl`) | MIT | Yes | Batch exports to parquet/Postgres/ClickHouse for historical RAG + analytics; maintenance slowing `[REVIEW]` | consider (one-off backfills only) |
| **OpenBB Platform** (`pip install openbb`) | **AGPL-3.0** (changed from Apache) | Yes | One typed API over many free providers (CoinGecko, DefiLlama, DexScreener, Bitquery…); **AGPL contaminates a closed bot if linked in-process**; large dep tree | skip for in-process; consider only as a separate containerized service if we consolidate providers (pushback rule: we already have the 5 we need) |
| **FinGPT / AI4Finance** | MIT | Yes | Finance-tuned LLM lineage (21k★, active); equities-oriented, not crypto-native; useful as fine-tune reference only | skip (Zen > FinGPT quality; no fine-tune budget) |
| **CryptoBERT** (`elKornio/CryptoBERT`) | `[REVIEW]` (research) | Yes | BERT sentiment classifier tuned on crypto tweets/news (bullish/bearish/neutral) — runs through **Infinity `classify`** we already serve; cheap sentiment signal for market-context answers | **consider** (S effort; add as a tool `sentiment(text)`) |
| **HF datasets** — `OpenBB/OpenBB-215K` (Apache-2.0, 215K bilingual finance LLM corpus, Feb 2026); DefiLlama samples (cc-by-4.0, unofficial, tiny) | varies | n/a | Ingestion corpora for finance-tuned RAG/continued-pretraining; no official maintained token-price parquet dumps exist `[REVIEW]` | consider (OpenBB-215K as a corpus experiment; otherwise build corpus from DefiLlama/api-docs + protocol docs per B4) |
| **Sentiment tooling** (twint/aitoa…) | — | — | Twitter scrapers are dead (X API restrictions); LunarCrush paid | skip `[REVIEW]` |
| **Solana** (Helios) | MIT (Helios is an **Ethereum** light client, not Solana — correction) | — | Cheap chain data beyond Helius free tier (25k req/mo) | skip for now (only if Solana volume outgrows free tier; solana-labs repo archived, canonical client is `anza-xyz/agave` Apache-2.0) |

**Recommendation:** extend what we have (DefiLlama python-sdk, Ponder, DefiLlama/api-docs as RAG corpus), add **Subsquid or Envio-Hypersync clients** for cheap historical on-chain backfill (license-review HyperIndex first), and **CryptoBERT via Infinity `classify`** as a cheap sentiment tool. Skip new indexer frameworks (The Graph/Trueblocks), skip OpenBB in-process (AGPL), skip sentiment scrapers.

---

## 7. Explicit skip list (avoid NIH traps)

- **New MCP servers** — fleet at 30/30 cap; these capabilities should be Docker services or in-process, per AGENTS.md hard rules.
- **LangChain / LlamaIndex as orchestration layer** — we already have targeted adapters; adopting the framework wholesale duplicates our hand-rolled, tuned pipeline.
- **RAGFlow / Dify** — full platforms; rewrite, not enhancement. (Note: a `dify-agents/` dir exists in repo — verify whether it's dead weight before it influences decisions.)
- **Heavy agent frameworks** (LangGraph/smolagents/CrewAI) — single-conversation bot, 1–2 hops, latency-bound.
- **Paid/API-first "OSS"** (jina API, Cohere rerank, Dune) — violates frugality rule.
- **New databases** — Qdrant + Postgres + ClickHouse + Neo4j + Dragonfly already cover vector/hybrid/analytics/graph/cache; no gap.

---

## 8. Suggested sequencing (aligned with BOT-IMPROVEMENT-PLAN)

1. **P0.3 (already planned):** fix rerank hang; Qdrant hybrid dense+BM25 RRF; MMR; one embedder. Add `json_repair` to tool-args parsing (30 min).
2. **Reranker upgrade** (independent, cheap): bge-reranker-v2-m3 → **gte-multilingual-reranker-base** on Infinity (S); later **Qwen3-Reranker-0.6B via TEI** for the bigger jump (M). [REVIEW] on Qwen3-Reranker serving: causal-LM scorer, needs TEI/vLLM, not Infinity.
3. **RAGAS harness** on the 50-query golden set (P1.5) — establishes the eval gate before any model/router change; wire 👍/👎 feedback into it.
4. **Phoenix** tracing container (S) — we already emit OpenInference spans; get dashboards before touching routing.
5. **P1.1 tool-use agent:** Pydantic AI as deep-path tool selector, regex stays fast-path; wrap existing registry handlers as tools; cap at 1–2 hops; budget per-hop.
6. **Embedder swap** to Qwen3-Embedding-0.6B *only if* re-embedding is scheduled; otherwise keep bge-m3 and bank the Qdrant gains.
7. **Graph RAG (LightRAG, Neo4j+Qdrant)** for entity-linked questions (wallet/exploit/entity) as a *separate* curated corpus — pilot on Rekt/Chainabuse/SlowMist data (B4).
8. **Subsquid/Envio clients + DefiLlama api-docs corpus + CryptoBERT** as ingestion backlog; revisit Trueblocks only if forensic depth becomes a product feature.

---

## 9. Source notes & verification status

All facts below verified live on 2026-08-14 (no general web search available in-session; direct registry/API checks used — more authoritative for licenses).

- LightRAG: MIT, 38.9k★, 9.6k commits, active through 2026-07; Qdrant + Neo4j backends; RAGAS + Langfuse integrations; rerank recommended local (github.com/HKUDS/LightRAG).
- Pydantic AI: MIT, 19.3k★, 2.7k commits, near-daily releases (v2.30.0 / 2.0 line); LiteLLM/OpenRouter/Ollama/DeepSeek providers; pydantic-evals (github.com/pydantic/pydantic-ai; pypi.org/pypi/pydantic-ai).
- RAGAS: Apache-2.0, 15.3k★, org vibrantlabsai; last release Feb 2026 (activity stall noted); metrics + test-set generation (github.com/vibrantlabsai/ragas; pypi.org/pypi/ragas 0.4.3).
- PyPI version/license spot-checks: ragas 0.4.3 (Apache-2.0), lightrag-hku 1.5.6 (MIT), instructor 1.15.4 (MIT), outlines 1.3.3 (Apache-2.0), deepeval 4.1.8 (Apache-2.0), pydantic-ai 2.30.0, json-repair 0.63.2 (MIT), **openbb 4.7.2 (AGPL-3.0)**.
- HF model spot-checks (api.models + MTEB registry): Qwen3-Embedding-0.6B (Apache-2.0 per model card, 8.1M downloads, MTEB-ML 64.33), Qwen3-Embedding-4B (3.75M, 2560d), Qwen3-Embedding-8B (2.84M, 4096d), Qwen3-Reranker-0.6B (2.63M, MTEB-R 65.80), Qwen3-Reranker-4B (2.76M, MTEB-R 69.76), gte-multilingual-reranker-base (974k, MTEB-R 59.51, Infinity-tested), **jina-reranker-v2-base-multilingual and jina-embeddings-v3 are CC-BY-NC-4.0 (non-commercial)** — excluded, multilingual-e5-large-instruct (2.03M, **MIT**, 512-ctx), nomic-embed-text-v2-moe (1.2M, **512-ctx max**), snowflake-arctic-embed-m-v2.0 (156k), BAAI/bge-reranker-v2.5-gemma2-lightweight (**Gemma ToU**, 9B-based), FlagEmbedding 1.4.0 (MIT); finance/crypto embeds (Fin-ModernBERT-RAG-embed-base, iioos/crypto-embedding-model) immature `[REVIEW]`.
- Infinity: MIT, 2.9k★; "deploy any HF embedding/reranking model"; tested reranker list includes bge-reranker-v2-m3 and gte-multilingual-reranker-base (Qwen3-Reranker not CI-tested — validate); also serves CLIP/ColPali late-interaction + text classification (e.g., ProsusAI/finbert) (github.com/michaelfeil/infinity).
- Crypto/DeFi verified corrections: hypersync moved to Envio (`enviodev`, clients MPL-2.0, HyperIndex license null); Subsquid Apache-2.0; Ponder MIT (1.1k★); The Graph Apache-2.0; TrueBlocks **GPL-3.0**; ethereum-etl **MIT** (slowing); DefiLlama python-sdk MIT, `defillama.com/datasets` 404, DefiLlama/api-docs MIT; OpenBB **AGPL-3.0**; FinGPT MIT (21k★); CryptoBERT (elKornio); OpenBB-215K dataset (Apache-2.0); solana-labs archived → anza-xyz/agave; Helios is an Ethereum light client (MIT), not Solana.
- Project-internal references: `docs/BOT-IMPROVEMENT-PLAN.md`, `docs/RAG_ARCHITECTURE.md`, `app/rag/*`, `app/domains/telegram/rugmunchbot/tools/*`.
