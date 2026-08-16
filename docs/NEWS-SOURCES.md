# News Sources — How They Are Gathered, Deduped, Ranked, Posted

> Canonical overview of the news pipeline: where sources come from, how the
> same story collapses to one article, how importance is scored, and where the
> result lands (Telegram news channel, main channel, Ghost CMS, website).
> Last updated: 2026-08-16 (source expansion: TG 33 → 64, RSS 57 → 84).

## 1. Gathering — three source lanes

The pipeline ingests from **three lanes**; every lane feeds the same
`read → normalize → dedupe → score → route` pipeline.

| Lane | Where the sources live | Mechanism |
|------|------------------------|-----------|
| **Databus RSS** | `app/databus/news_provider.py` → `TOP_50_FEEDS` (84 feeds) + `app/databus/global_news.py` (Google/Bing/major-news) + `app/databus/unified_news_sources.json` (registry, 312 entries) | `refresh_news_cache` fetches RSS in batches (fail-open per feed), stores to Redis `rmi:news:full_feed` served by `/api/v1/news/feed` + `/headlines`; `app/domains/news/ingest.py` persists RSS items into `news_items` + RAG |
| **Telegram channels** | `app/domains/telegram/rugmunchbot/channels/telegram_news/_sources.py` → `_DEFAULT_CHANNELS` (64 curated public channels: news outlets, security/exploit alerts, DeFi/protocol feeds, on-chain/alpha) | Headless Telethon user-session sweep (`app.domains.news_ingest.read_channel_messages`), every channel fails open; auto-disabled after 3 consecutive read failures for a 24h cooldown (`note_channel_failure`) |
| **Operator account (cryptorugmunch)** | Auto-discovery via `app.domains.news_ingest.discovery.discover_followed_channels` — enumerates the operator's followed/member public channels (`client.get_dialogs()`), gated by `RMI_TG_NEWS_AUTO_DISCOVER` (default **on**), bounded by `RMI_TG_NEWS_MAX_CHANNELS` (40) | Wired + categorized: each discovered channel is classified by `classify_channel_category` → `news` / `alpha` / `scam` / `other`. **news** → swept as `crypto_news`, **alpha** → swept as `alpha`, **scam** → left to scam_watch, **other** → skipped. Discovered channels flow into the **exact same** dedupe → score → RAG pipeline as the explicit list. When discovery yields nothing (disabled / no client / no creds), the sweep falls back to the explicit `_DEFAULT_CHANNELS` / `RMI_TG_NEWS_CHANNELS` env list. |

## 2. Dedup — four layers

1. **In-batch** (`dedupe_articles`): multi-key — canonical source URL (first
   non-`t.me` link in the message) → channel `t.me` URL → normalized title
   (lowercase, punctuation stripped, first 60 chars) → content hash. A story
   rephrased or forwarded across channels collapses to one article.
2. **Cross-run (Redis)** (`filter_seen_articles`): checks the same key set
   against the shared `app.rag.dedup` ZSETs (`rag:dedup:content:tg_news`), so a
   story seen in a previous sweep — even rephrased — is never re-read/re-embedded.
   Articles are marked **only after** a successful `news_items` insert.
3. **Cross-run (Postgres)**: `news_items.news_id` derives from the canonical
   `https://t.me/<channel>/<message_id>` URL — re-sweeps are skipped by the
   primary-key guard.
4. **Post-level**: the scheduler's in-memory `_remember` set stops the digest
   re-posting the same article on the next tick.

## 3. Rerank — two scorers

- **Rerank** (`channels/importance.py`, Infinity `BAAI/bge-reranker-v2-m3`):
  the primary gate for what gets posted (`RMI_NEWS_IMPORTANCE_THRESHOLD`;
  fail-open heuristic fallback when Infinity is down).
- **Lightweight** (`app.domains.news_ingest.lightweight_importance`):
  deterministic 0-1 score (source authority, breaking markers, crypto-relevant
  keywords, freshness) used as stable secondary ordering + website feed items.

`channels/router.py::route_news` sends every scored story to exactly one
destination: `skip` (< floor) / `news` (news band) / `main` (≥ 0.7) / `alpha`
(alpha category below the main gate).

## 4. Posted — where the story lands

- **news channel** — news-band stories: breaking top story posts solo (🚨),
  otherwise a feature card / themed roundup / top-3 hybrid / numbered digest.
- **main channel** (@cryptorugmuncher) — high-importance (≥ 0.7) stories post
  solo (breaking shape or ⭐ feature card).
- **alpha channel** — alpha-category stories (whale/alpha channels), when an
  alpha chat id resolves.
- **ghost** — Ghost CMS cross-post (`personal_channel.ghost_publish_article`,
  gated on `GHOST_ADMIN_API_KEY`; fail-open).
- **website** — every swept article persists to `news_items` (served by
  `/api/v1/news/*`); TG items are merged into the Redis full-feed cache
  (`inject_into_news_cache`) with `kind="telegram"` + channel attribution; a
  market-awareness snapshot publishes to `rmi:news:market_awareness`.
- **RAG** — swept articles cluster into stories and embed into the `news_v2`
  collection (`ingest_into_rag`), the collection the bot's `get_news` RAG tool
  searches.

## 5. Adding a source

### Telegram channel (recommended)
1. Probe the handle: `curl -s https://t.me/s/<handle>` — a page titled
   `"<Name> – Telegram"` = public (View), `"Telegram: Contact @<handle>"` =
   exists but preview disabled (needs the sweep account to be a member —
   mark `[REVIEW]`).
2. Append the handle to `_DEFAULT_CHANNELS` in
   `app/domains/telegram/rugmunchbot/channels/telegram_news/_sources.py` with a
   one-line comment; keep the per-tier grouping.
3. If the operator account already follows it, auto-discovery picks it up with
   **no code change** (categorization by title/username keywords — add a keyword
   to `_CHANNEL_NEWS_KEYWORDS` / `_CHANNEL_ALPHA_KEYWORDS` only if the title
   classifies as `other`).
4. Update `tests/domains/test_rugmunchbot_tg_news.py`
   `test_defaults_have_curated_handles` count assertion.
5. Dead/renamed handles are handled automatically: 3 consecutive read failures
   → 24h auto-disable cooldown; comment the handle out of the default list.

### RSS / databus feed
1. Probe the feed (HTTP 200 + feedparser parse + fresh items ≤ 30 days) — the
   standard is `scripts/test_news_sources.py`.
2. Add the `(url, name)` tuple to `TOP_50_FEEDS` in `app/databus/news_provider.py`
   (or to `app/databus/global_news.py` for Google/Bing/major-news lanes).
3. For the master registry, add a source entry to
   `app/databus/unified_news_sources.json` (id / name / url / category / type)
   and reconcile `total_sources` + `categories`; re-run the probe to refresh
   `news_sources_status.json` and prune DEAD entries with
   `app/databus/news_source_health.py::apply_prune`.

No code change is required to add sources the operator account already follows:
that is the entire point of the auto-discovery lane — drop in the deployment
`RMI_TG_NEWS_CHANNELS` (comma/pipe-separated) or just have the operator join
the channel.
