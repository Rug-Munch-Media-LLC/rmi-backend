# News Page Design — "Beat CryptoPanic + GroundNews"

Status: design · owner=RugMunch Engineering · date=2026-08-13 · READ-ONLY (no code merged)

Goal: a news page with (1) image+category feed, (2) sentiment/bias ranking, (3) per-article comments/reactions, (4) prominent newsletter aggregation, (5) data model + API, (6) degodded frontend.

## 0. Current state (read from code)

- **Backend** `app/domains/news/`: `news_service/` live aggregator (170+ sources: RSS, Reddit hot, CoinGecko, CryptoPanic, Ghost, internal scanner/alerts; in-memory cache TTL 180s, no persistence). `ratings.py` computes `ArticleRating` (bias 5-point, sentiment −100..+100, factual, tier, confidence) from a 90-source `SOURCE_CREDIBILITY` table but **enrichment is not persisted**. `router.py`: `POST /api/v1/news` (filters, `clustered=true`), `GET /trending` (score = recency_decay × source_authority × social_velocity × |sentiment|), `GET /{news_id}`, `POST /{news_id}/analyze`. `clusterer.py` T03: MinHash+DBSCAN → `news_clusters` (the GroundNews story-grouping primitive already exists).
- **Catalog** `NewsItem` (news_items table): news_id, url, title, summary, body_markdown, source, published_at, chains/tokens/wallets, sentiment_score, ai_analysis, rag_embedding_id. **No image_url, category, bias, factual, tier, or engagement columns.**
- **Newsletter** domain is fully built: `Newsletter`, `NewsletterSub`, `NewsletterPayment`, `NewsletterDelivery`, `NewsletterCrosspost`; router: create/list/get/send/schedule/delivery-status. Reuse, don't rebuild.
- **Comments/reactions: none exist anywhere** in the backend.
- **Frontend** `src/pages/NewsPage.tsx` (804 lines): monolithic — 6 API calls (`getNewsFeed`, `getNewsSentiment`, `getNewsSources`, `getNewsTrending`, `getNews`, `getNewsCombined`), ~12 helper fns, inline `NewsCard`, bias/factual color maps, severity inference. No comments, no newsletter section, no category tabs (only color chips), no bias-spectrum UI.

## 1. Feed: images + categories

- **Images**: add background OG-image enrichment to RSS ingest (`fetchers_rss.py`): parse `<media:content>`, `<enclosure>`, OG tags; fallback chain → article OG → source favicon → generated gradient placeholder. Persist to `news_items.image_url`. Social items (Reddit/Twitter) already carry thumbnails.
- **Categories**: normalize current free-form keywords (`CATEGORY_KEYWORDS`, reddit sub map) into a fixed taxonomy: `market | security | protocol | defi | ai | tech | regulatory | nft | whale | general`, each with sub-tags (chains/tokens already indexed). Store `category` column; category filter chips on the page.
- **Persistence**: move hot-path from in-memory aggregator to `news_items` (background ingest worker upserts; `/news/feed` reads Postgres, aggregator remains the fallback). Cursor pagination, `since_hours` window.

## 2. Sentiment + bias ranking

- **Persist ratings**: add `bias, factual_rating, source_tier, rating_confidence, sentiment_label` columns to `news_items`; run `ratings.enrich_article` at ingest, not per-request.
- **Sentiment upgrade**: keep keyword scoring as fast path; batch LLM re-scoring of headlines (LLMRouter, `POST /analyze` already exists) for tier-1 items, stored in `ai_analysis`/`sentiment_score`.
- **Ranking modes** (extend `sort` enum): `trending` (existing time-decay + engagement term from reactions), `sentiment` (extremes first), `controversy` (|sentiment| × engagement × 1/confidence — polarizing + disputed ranks up), `bias` (per-source bias filter/group).
- **Bias spectrum (GroundNews killer feature)**: per story cluster (`news_clusters` exists), return the *distribution* of sources by bias with links — coverage panel: "9 sources, 3 Lean Left, 4 Center, 2 Lean Right". Blindspot detection: cluster where one bias bucket dominates.

## 3. Comments + reactions

- **New tables**:
  - `news_reactions(news_id, user_id, vote in ('like','dislike'), created_at, PK(news_id,user_id))` — counts via `COUNT(*)` + cached in `news_items.upvotes/downvotes`.
  - `news_comments(id UUID, news_id FK, user_id, parent_id nullable (threading), body text ≤2000, created_at, updated_at, deleted_at nullable, status in ('visible','hidden'))` + `news_comment_reactions(comment_id, user_id, vote, PK(comment_id,user_id))`.
- **Endpoints**: `POST/DELETE /api/v1/news/{news_id}/reactions` (upsert vote), `GET /news/{id}/reactions` (counts + caller's vote), `GET/POST /api/v1/news/{news_id}/comments?parent_id=&cursor=`, `DELETE /comments/{id}`, `POST/DELETE /comments/{id}/reactions`. Auth via existing unified auth (wallet/x402 session). Rate-limit comments (e.g. 5/min) and run profanity/spam filter before persist.
- **Engagement in ranking**: reactions feed `social_velocity`/controversy terms (compute from table, not the current hardcoded 0).

## 4. Newsletter aggregation

- **Prominent section**: "Newsletters" hub tab + homepage strip.
- **Internal**: reuse newsletter domain — published/sent issues (`status='sent'`, cover_image, excerpt, tags, stats) rendered as cards with subscribe CTA (`newsletter_subs`).
- **External**: curated link-cards for top crypto newsletters (Bankless, Milk Road, Blockworks, The Defiant) — metadata-only table `newsletter_links(id, title, url, cover_image, author, tags, weekly_frequency)`, no scraping.
- **Endpoints**: `GET /api/v1/newsletters/feed` (published issues + external links, paginated), `POST /api/v1/newsletters/{id}/subscribe` (reuse existing), `GET /api/v1/newsletters/subscriptions/me`.

## 5. Data model summary (new/changed)

| Table | Change |
|---|---|
| `news_items` | +image_url, category, bias, factual_rating, source_tier, rating_confidence, sentiment_label, upvotes, downvotes, comment_count |
| `news_clusters` | (exists) + bias_distribution JSON, blindspot flag |
| `news_reactions` | NEW |
| `news_comments` | NEW (+ news_comment_reactions) |
| `newsletter_links` | NEW (external aggregation) |
| newsletters / newsletter_subs | unchanged (reuse) |

Migrations: expand/contract, backfill image/category/ratings via re-ingest job. `[REVIEW]` — see flags.

## 6. Frontend component architecture (degod NewsPage.tsx)

```
src/features/news/
  NewsPage.tsx            orchestrator: tabs [Feed | Trending | Bias | Newsletters], layout grid
  api/news.ts             typed client (getNewsFeed, getTrending, getClusters, reactions, comments, newsletters)
  hooks/
    useNewsFeed.ts        React Query: cursor pagination, category/sort filters, optimistic reactions
    useNewsComments.ts    React Query: thread tree, optimistic post/delete, optimistic votes
  components/
    NewsFeed.tsx          virtualized list (react-window) + filter chips + view toggle
    NewsCard.tsx          image, category badge, severity, excerpt, reading time (extracted from page)
    NewsRankingBar.tsx    sentiment score −100..+100, bias tag, factual tier, confidence (extracted helpers)
    StoryClusterPanel.tsx GroundNews-style bias-spectrum coverage for clustered stories
    ReactionBar.tsx       like/dislike + counts, optimistic; reuses SocialActionBar for share
    CommentSection.tsx    drawer/thread, markdown-light, moderation badges
    NewsletterHub.tsx     issue cards + external links + subscribe CTA (new)
  lib/ratings.ts          color maps, formatTimeAgo, stripHtml, severity inference (moved from page)
```
Rules: page file ≤120 lines (orchestration only); all presentational components pure + storybook; API layer typed from OpenAPI. Keep `SocialActionBar`.

## [REVIEW] flags

1. `news_items` schema change + backfill migration is required before any of this lands — confirm expand/contract policy with fleet-infra (Squawk gate).
2. Reactions/comments have **no existing auth pattern in the news domain** — must bind to unified auth (wallet/x402); anonymous visitors? Decide guest read-only.
3. `social_velocity` in trending is currently hardcoded 0 — reaction counts will be zero at launch until backfilled; seed with historical share counts or accept cold start.
4. LLM sentiment re-scoring cost: batch at ingest for tier-1 only, cap per day (frugality rule); keyword path must stay the default.
5. `clusterer.py` window is 30-min MinHash — long-running stories (multi-day) won't cluster; evaluate widening window for the bias-spectrum panel.
6. Newsletter hub: external newsletters are curated link-cards, not auto-ingested — content accuracy is manual; rate-limit subscribe endpoint to prevent spam signups.
7. Bias ratings are source-table heuristics, not per-article analysis — flag low confidence (<0.5) in UI rather than hiding it (CryptoPanic-style honesty).
