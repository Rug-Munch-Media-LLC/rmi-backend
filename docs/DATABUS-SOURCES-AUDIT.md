# DataBus News/Intel/Social Sources — Wiring Audit

**Auditor:** code-explorer (read-only) · **Date:** 2026-08-13 · **Scope:** `app/databus/`, `app/domains/databus/`, `app/rag/`, `app/intel_*.py`, `app/github_rag_feeder.py`, `scripts/unified_rag_ingest.py`

---

## 1. Total registered news/intel/social sources

| Registry | Count | Location |
|---|---|---|
| **Unified source registry** (the "200+ sources" claim) | **255** (219 rss, 21 reddit, 15 nitter; 123 verified / 132 unverified) | `app/databus/unified_news_sources.json` |
| NewsService RSS feeds | 177 | `app/domains/news/news_service/sources.py` (RSS_FEEDS) |
| NewsService Reddit subreddits | 33 | same file (REDDIT_SUBREDDITS) |
| MegaNews RSS (legacy) | 49 | `app/databus/mega_news.py` |
| IntelFeedPipeline threat feeds | 15 | `app/intel_feed_pipeline.py` (FEEDS) |
| NewsIntel config entries | 11 | `app/databus/news_intel/config.py` (NEWS_SOURCES) |
| SocialFeeds (Reddit + Nitter) | 10 + 15 | `app/databus/social_feeds.py` |
| CT Rundown accounts | 136 handles / 6 tiers | `app/databus/x_intel.py` (CT_ACCOUNTS) |
| GitHub RAG repos | 6 | `app/github_rag_feeder.py` (SOURCES) |

**Headline count: 255 registered sources** — basis for the "200+ sources" claims in `app/routers/news.py:5`, `app/routers/intelligence_panel.py:8`, `app/databus/provider_implementations/passthrough.py:40` (note: `docs/ARCHITECTURE.md:9` "96+ data providers" is a *different* claim covering all databus categories, not news).

## 2. Wired (fetched → normalized → stored in RAG/store)

| Path | Sources | Store | Meta/headline/link? |
|---|---|---|---|
| **RAG crawler** `app/rag/crawler.py discover_and_ingest` → `load_sources()` reads the 255-registry, keeps only crawlable types → `crawl_and_ingest` → `engine.ingest_document` | **219 rss** of registry | Qdrant `news_v2` + Redis doc store + ANN | ✅ source_url + title + summary + rating card |
| **NewsService** `app/routers/news.py /api/v1/news/feed` (Redis `rmi:news:full_feed`, 5-min background refresh via `news_provider.refresh_news_cache`) + `news_intel.aggregate_all_news` → databus chains `news`, `news_intel` | 177 RSS + 33 Reddit + CoinGecko/CryptoPanic/Ghost/scanner/alerts + 5 RSS (news_intel config) + x_crypto | Redis cache; nightly → `news_articles` collection (Redis + ANN) | ⚠️ headline+source+published, **NO url/link** |
| **IntelFeedPipeline** `run_cycle` (15 feeds) | 15 threat feeds + Birdeye on-chain scan | Redis `rag:market_intel` / `rag:token_analysis` (7d TTL) | ✅ url + source + severity + entities |
| **CT Rundown** `x_intel.fetch_ct_rundown` → nightly RAG | 136 CT accounts | `ct_rundown` collection | ⚠️ headline (tweet text) + author, **NO link** |
| **Social metrics** `social_intel.get_social_metrics` → nightly RAG | aggregated | `social_intel` snapshot | snapshot only (N/A) |
| Databus `news` chain fallbacks `_messari_news` / `_coindesk_news` | 2 API providers | key-gated (`MESSARI_API_KEY`/`COINDESK_API_KEY`) | ✅ url+title |

## 3. Registered but NOT wired (dead)

- **36 social sources in the unified registry** (21 reddit + 15 nitter) — `_CRAWLABLE_TYPES = ("rss","atom","sitemap")` in `app/rag/_crawler_constants.py:24` excludes them; no other consumer reads the JSON. `social_feeds.py` (10+15) is separate and has no app-level caller.
- **132 of 255 registry entries unverified** (`verification.needs_verification: 132`, last check 2026-08-03).
- **`app/github_rag_feeder.py` (6 repos)** — DEPRECATED; `ingest_repo` is a no-op `return 0` (`:195-202`).
- **`scripts/unified_rag_ingest.py`** — cron `0 3 * * *` posts to `/api/v1/rag/ingest` + `/api/v1/databus/fetch`; current facade is `/api/v1/rag/v2/ingest` (`app/api/v1/rag/search.py:24,45`) and the databus router has no `/fetch` handler → **likely 404 / dead daily ingest**.
- **`app/databus/mega_news.py` (49 feeds)** and **`global_news.py`** — standalone; no app-level caller found (only `__main__`). mega_news still writes Postgres `crypto_news` if run manually.
- **news_intel config entries `messari`, `cryptocompare`, `lunarcrush`** (`config.py:66-98`) — declared `type: api` but no fetcher is attached in `aggregate_all_news` (only `type=="rss"` + cryptopanic + x + news_service are gathered, `__init__.py:212-227`).

## 4. Missing meta/headline/link per wired source

- `news_articles` (nightly_rag_index `rag_ingestion.py:41-53`): text = title+summary; metadata = source/categories/sentiment/quality/published — **url dropped** (used only for the hash id). ❌
- `ct_rundown` (`rag_ingestion.py:68-82`): no url/link in metadata. ❌
- `market_intel` / `social_intel`: snapshots — acceptable, not articles.
- `intel_feed_pipeline` ✅ and crawler `news_v2` ✅ both carry links.

## 5. Prioritized wiring plan

1. **P0 — Fix nightly RAG metadata**: add `url` to `news_articles` and `ct_rundown` docs in `app/databus/rag_ingestion.py` (hashes already keyed on url; one-line metadata add) so every indexed article is citable.
2. **P0 — Repair `scripts/unified_rag_ingest.py`**: retarget to `/api/v1/rag/v2/ingest` (or call `app.rag.engine` directly); verify the 3AM cron actually runs.
3. **P1 — Wire unified-registry social sources**: extend `_CRAWLABLE_TYPES` handling or add a reddit/nitter fetcher consuming `unified_news_sources.json` (e.g., via `social_feeds.py`) into a `social_articles` collection.
4. **P1 — Merge legacy pipelines**: fold `mega_news.py`/`global_news.py` feeds into `news_service.sources.RSS_FEEDS` (dedup by URL) or the unified registry; delete or wire their callers.
5. **P1 — Re-enable GitHub feeder**: port `github_rag_feeder.py` to the v2 facade (`/api/v1/rag/v2/bulk-ingest`) or replace with the live `scripts/ingest_free_scam_sources.py` path; wire into `unified_rag_ingest.py`.
6. **P2 — Key-gated sources**: confirm `MESSARI_API_KEY`, `COINDESK_API_KEY`, `CRYPTOPANIC_API_KEY`, `X_API_KEY` in env; attach cryptopanic/x to scheduled RAG path; add fetchers for `cryptocompare`/`lunarcrush` config entries.
7. **P2 — Verification pass**: re-verify the 132 unverified registry URLs (script exists in the JSON `verification` block — 8-parallel curl).

## 6. [REVIEW] flags / uncertainty

- **Scheduler ownership unverifiable in-repo**: `nightly_rag_index` ("cron 3AM"), `IntelPipeline.run_cycle` ("every 30 min"), `run_social_scan` ("every 6h"), and the crawler sweep ("crawl-crypto-news.sh") are documented as external cron/Hermes jobs; no in-repo scheduler wiring was found — confirmed only for `unified_rag_ingest.py` (`0 3 * * *` in its docstring).
- **`unified_rag_ingest.py` endpoint liveness** — the 404 hypothesis is based on route inspection (`/api/v1/rag/v2/*` only); not runtime-tested.
- Names mangled to "n" in some grep output (display artifact); all counts were re-verified by direct reads + AST/regex counts.
