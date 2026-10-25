# Content Quality — measured, not assumed

Window: live `news_items` on Talos Postgres, measured 2026-10-25 (read-only SQL).
Every number below is a query result, not an estimate. Anything unmeasured is marked so.

## Metrics

| Metric | Value | Source query |
|---|---|---|
| Story volume (7d) | 10,172 (~1,453/day) | `count(*) where ingested_at > now()-'7 days'` |
| Story volume (30d) | 13,902 (~464/day) | same, 30d |
| Distinct sources | 233 | `count(distinct source)` |
| Source attribution present | 100% (`source` NOT NULL), but `canonical_url` NULL **97.8%**, `source_type` NULL **5.0%** | 30d |
| Timestamp present | 100% NOT NULL — *by construction, not by fact*: undated entries were stamped with the ingest clock | code: `ingest.py` |
| Fabricated-freshness proxy (`published_at` within ±60s of `ingested_at`) | **4.6%** of 30d (~638 items) | 30d |
| Duplicate rate, normalised title (7d) | **14.0%** | 7d |
| Duplicate rate, URL/canonical_url (7d) | **10.6%** | 7d |
| Stale at ingest: `published_at` older than 24h | **22.9%** | 30d |
| Stale at ingest: older than 7d | **11.0%** | 30d |
| Thin body (<200 chars) | **45.3%** | 30d |
| Telegram alerts sent (7d) | 3 | `alerts` |
| Alert dedupe ratio | **not measured** — `alerts` has no `news_id` FK, so alert↔item staleness cannot be joined | [REVIEW] |

## Top 3 defects by impact

1. **Undated items were counted as fresh (FIXED).** `ingest.py` did `pub_dt = utcnow()` when the feed carried no
   `published`/`updated`, making an undated aggregator row indistinguishable from breaking news. Floor measured:
   4.6% of 30d items sit within ±60s of their ingest time; the true share is higher because a feed that omits the
   date entirely leaves no trace.
2. **Duplicates slip past dedupe (OPEN, biggest volume defect).** The item key is `sha256(source:url)`, so the same
   story from two outlets is always two rows: 14.0% normalised-title duplicates and 10.6% URL duplicates in 7d.
   Worst offender: `ETH Magicians` 232 rows / 68 unique titles (71% in-source duplication).
3. **No age gate at ingest (OPEN).** 22.9% of items are already >24h old when ingested, 11.0% >7d. Nothing rejects
   or demotes them, and 45.3% carry no usable body. Whether Telegram alerts fire on these is **unproven** — only 3
   alerts in 7d and the schema cannot join them to items [REVIEW].

## Fix shipped — unknown freshness stays unknown

- `app/domains/news/freshness.py` — `resolve_published_at(entry)` returns `None` when the feed has no parseable
  timestamp. No ingest clock, ever.
- `app/domains/news/freshness_window.py` — `freshness(published_at, ingested_at)` → `fresh | stale | unknown`;
  future-dated garbage (>1h skew) is `unknown`.
- `app/domains/news/ingest.py` — uses both; `published_at` is now nullable.
- `alembic/versions/c19a1b2d3e4f_news_items_published_at_unknown.py` — UP drops NOT NULL; DOWN backfills
  `published_at = ingested_at` (documented, reversible loss).
- `scripts/content_quality_replay.py` + `tests/fixtures/news_entries_freshness.json` — replayable delta.

Replay on fixture (n=10: 4 dated fresh, 2 dated stale, 4 undated):

| metric | legacy | fixed |
|---|---|---|
| fresh | 8 | 4 |
| stale | 2 | 2 |
| unknown | 0 | 4 |
| fresh share % | 80 | 40 |

Live expectation: ~638 items/30d move from "fresh" to `unknown` and leave every fresh-share count. No re-ingest needed.

## Still wrong / next

- Cross-source dedupe by normalised title + entity (tickers/chains) similarity, not `source:url` — target <3% title dupes.
- Ingest age gate: reject/quarantine `published_at < ingested_at - 7d` and count rejects per feed.
- Backfill `canonical_url` (97.8% NULL) so URL dedupe is meaningful.
- Add `news_id` to `alerts` so alert staleness becomes a measurable number.
- Feed ordering must treat `published_at IS NULL` explicitly (never "now") — audit `app/domains/news/feed_source.py`.

## X (Twitter) slot — expected, unproven

No X payload is stored yet: `news_items.tweet_id` exists but the lane is not live, so X contributes **0 measured
stories**. When the pry-backed X scrape lands, add to the table above: X items/day, % with a resolvable tweet
timestamp, % duplicates against the RSS/Telegram lanes (same story via a KOL), and % attribution to the original
poster vs the reposter. Until then X is excluded from every count on this page — do not estimate it.
