-- X ingestion lane — additive, nullable, idempotent.
-- source_type: 'x' marks posts that came from the X lane (NULL for every
-- pre-existing row, so no backfill is implied and no row changes meaning).
-- tweet_id: the status id, NULL when the transport could not expose one.

ALTER TABLE news_items ADD COLUMN IF NOT EXISTS source_type text;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS tweet_id text;

CREATE INDEX IF NOT EXISTS news_items_source_type_published_idx
  ON news_items (source_type, published_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS news_items_x_tweet_uniq
  ON news_items (tweet_id) WHERE source_type = 'x' AND tweet_id IS NOT NULL;
