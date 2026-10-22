-- News feed ranking report — read-only. Before/after top-20 for one window.
-- Mirrors app/domains/news/ranking.py exactly:
--   score = recency_decay(8h half-life) x source_trust x category_interest x engagement
--   then cross-source dedupe (normalised title) + max 3 stories per source
-- usage: psql -U rmi -d rmi -v window_hours=168 -f scripts/reports/news_feed_ranking_report.sql
\pset pager off

WITH base AS (
  SELECT n.news_id, n.title, n.source, n.category, n.published_at, n.source_trust,
         md5(lower(regexp_replace(regexp_replace(n.title, '<[^>]*>', '', 'g'),
                                  '[^a-zA-Z0-9]+', '', 'g'))) AS title_key
  FROM news_items n
  WHERE n.published_at > now() - (:'window_hours' || ' hours')::interval
    AND n.published_at <= now()
    AND btrim(COALESCE(n.title, '')) <> ''
    AND NOT (n.news_id LIKE 'tg:%' OR n.url LIKE '%t.me/%')
),
scored AS (
  SELECT b.*,
    0.5 ^ (EXTRACT(EPOCH FROM (now() - b.published_at)) / 3600.0 / 8.0) AS recency_decay,
    LEAST(1.0, GREATEST(0.05, COALESCE(b.source_trust, 0.45))) AS trust,
    CASE lower(COALESCE(b.category, ''))
      WHEN 'exploit' THEN 1.30 WHEN 'scam' THEN 1.25 WHEN 'regulation' THEN 1.15
      WHEN 'research' THEN 1.05 WHEN 'market' THEN 1.00 WHEN 'news' THEN 0.90
      WHEN 'shilling' THEN 0.35 ELSE 0.90 END AS cat_interest
  FROM base b
),
final AS (
  SELECT s.*, s.recency_decay * s.trust * s.cat_interest AS score FROM scored s
),
dedup AS (   -- one row per normalised title: highest trust, then earliest
  SELECT DISTINCT ON (title_key) *
  FROM final
  ORDER BY title_key, trust DESC, published_at ASC
),
by_score AS (
  SELECT row_number() OVER (ORDER BY score DESC) AS pos, * FROM dedup
),
after20 AS (
  SELECT * FROM by_score r
  WHERE (SELECT count(*) FROM by_score x WHERE x.source = r.source AND x.pos <= r.pos) <= 3
)
SELECT 'BEFORE (published_at DESC)' AS view, q.pos, q.title, q.source, q.category,
       round(score::numeric, 4) AS score
FROM (
  SELECT row_number() OVER (ORDER BY published_at DESC) AS pos, * FROM final
) q WHERE pos <= 20
UNION ALL
SELECT 'AFTER (scored, deduped, source cap 3)', z.pos2 AS pos, z.title, z.source, z.category,
       round(z.score::numeric, 4)
FROM (
  SELECT row_number() OVER (ORDER BY pos) AS pos2, * FROM after20
) z WHERE z.pos2 <= 20
ORDER BY view DESC, pos;

-- Duplicate groups collapsed in the same window
WITH base AS (
  SELECT md5(lower(regexp_replace(regexp_replace(title, '<[^>]*>', '', 'g'),
                                  '[^a-zA-Z0-9]+', '', 'g'))) AS title_key
  FROM news_items
  WHERE published_at > now() - (:'window_hours' || ' hours')::interval
    AND published_at <= now() AND btrim(COALESCE(title, '')) <> ''
)
SELECT count(*) AS raw_rows,
       count(DISTINCT title_key) AS stories,
       count(*) - count(DISTINCT title_key) AS collapsed_rows,
       round(100.0 * (count(*) - count(DISTINCT title_key)) / count(*), 1) AS collapsed_pct
FROM base;

-- Category mix of the deduped stories
WITH base AS (
  SELECT category, md5(lower(regexp_replace(regexp_replace(title, '<[^>]*>', '', 'g'),
                                  '[^a-zA-Z0-9]+', '', 'g'))) AS title_key
  FROM news_items
  WHERE published_at > now() - (:'window_hours' || ' hours')::interval
    AND published_at <= now() AND btrim(COALESCE(title, '')) <> ''
), dedup AS (
  SELECT DISTINCT ON (title_key) category FROM base ORDER BY title_key
)
SELECT COALESCE(NULLIF(category, ''), '(null)') AS category, count(*) AS stories
FROM dedup GROUP BY 1 ORDER BY stories DESC;
