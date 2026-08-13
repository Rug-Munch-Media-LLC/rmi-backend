"""Catalog database schema - initial migration.

Creates the tables referenced by the v4.0 catalog models.
Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA = """
-- Tokens (T27)
CREATE TABLE IF NOT EXISTS tokens (
    token_id           TEXT PRIMARY KEY,
    chain              TEXT NOT NULL,
    address            TEXT NOT NULL,
    symbol             TEXT,
    name               TEXT,
    decimals           INT,
    deployer_wallet_id TEXT,
    deployed_at        TIMESTAMPTZ NOT NULL,
    initial_supply     BIGINT,
    current_supply     BIGINT,
    is_honeypot        BOOLEAN,
    is_mintable        BOOLEAN,
    is_proxy           BOOLEAN,
    tax_buy_bps        INT,
    tax_sell_bps       INT,
    risk_tier          TEXT,
    risk_score         INT,
    risk_factors       TEXT[],
    rag_embedding_id   TEXT,
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tokens_chain ON tokens(chain);
CREATE INDEX IF NOT EXISTS idx_tokens_deployer ON tokens(deployer_wallet_id);
CREATE INDEX IF NOT EXISTS idx_tokens_risk ON tokens(risk_score DESC) WHERE risk_score IS NOT NULL;

-- Alerts
CREATE TABLE IF NOT EXISTS alerts (
    alert_id     TEXT PRIMARY KEY,
    token_id     TEXT,
    wallet_id    TEXT,
    chain        TEXT,
    alert_type   TEXT NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT,
    evidence     JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alerts_token ON alerts(token_id);
CREATE INDEX IF NOT EXISTS idx_alerts_wallet ON alerts(wallet_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);

-- News items (T28)
CREATE TABLE IF NOT EXISTS news_items (
    news_id          TEXT PRIMARY KEY,
    url              TEXT NOT NULL,
    title            TEXT NOT NULL,
    summary          TEXT,
    body_markdown    TEXT,
    source           TEXT NOT NULL,
    published_at     TIMESTAMPTZ NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    chains_mentioned TEXT[] DEFAULT ARRAY[]::TEXT[],
    tokens_mentioned TEXT[] DEFAULT ARRAY[]::TEXT[],
    wallets_mentioned TEXT[] DEFAULT ARRAY[]::TEXT[],
    sentiment_score  REAL,
    bias             TEXT,
    sentiment_label  TEXT,
    factual_rating   TEXT,
    source_tier      INT,
    rating_confidence REAL,
    ai_analysis      TEXT,
    rag_embedding_id TEXT,
    body_tsv         tsvector
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news_items(source);
CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news_items(sentiment_score);
CREATE INDEX IF NOT EXISTS idx_news_bias ON news_items(bias);
CREATE INDEX IF NOT EXISTS idx_news_factual ON news_items(factual_rating);
CREATE INDEX IF NOT EXISTS idx_news_body_tsv ON news_items USING gin(body_tsv);

-- Migrate pre-existing DBs that created news_items before the rating
-- columns existed (CREATE TABLE IF NOT EXISTS is a no-op there).
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS bias TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS sentiment_label TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS factual_rating TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS source_tier INT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS rating_confidence REAL;

-- Auto-update the body_tsv column on insert/update
CREATE OR REPLACE FUNCTION news_items_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.body_tsv := to_tsvector('english', COALESCE(NEW.title, '') || ' ' ||
                                COALESCE(NEW.summary, '') || ' ' ||
                                COALESCE(NEW.body_markdown, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS news_items_tsv_trigger ON news_items;
CREATE TRIGGER news_items_tsv_trigger
BEFORE INSERT OR UPDATE ON news_items
FOR EACH ROW EXECUTE FUNCTION news_items_tsv_update();

-- Scan reports (T29)
CREATE TABLE IF NOT EXISTS scan_reports (
    report_id          TEXT PRIMARY KEY,
    subject_type       TEXT NOT NULL,
    subject_id         TEXT NOT NULL,
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generated_by_model TEXT NOT NULL,
    risk_score         INT NOT NULL,
    risk_tier          TEXT NOT NULL,
    sections           JSONB DEFAULT '{}'::jsonb,
    markdown_url       TEXT,
    paid_via_x402      TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_subject ON scan_reports(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_reports_generated ON scan_reports(generated_at DESC);

-- RAG findings metadata (Qdrant has the vectors; this is metadata for query)
CREATE TABLE IF NOT EXISTS rag_findings (
    finding_id      TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL,
    source_url      TEXT,
    source_token_id TEXT,
    source_wallet_id TEXT,
    claim           TEXT NOT NULL,
    confidence      REAL NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    qdrant_point_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_token ON rag_findings(source_token_id);
CREATE INDEX IF NOT EXISTS idx_findings_wallet ON rag_findings(source_wallet_id);

-- x402 receipts (T34)
CREATE TABLE IF NOT EXISTS x402_receipts (
    tx_hash     TEXT PRIMARY KEY,
    agent_id    TEXT,
    tool        TEXT NOT NULL,
    amount_usd  REAL NOT NULL,
    chain       TEXT,
    paid_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tier        TEXT
);
CREATE INDEX IF NOT EXISTS idx_x402_paid_at ON x402_receipts(paid_at DESC);
CREATE INDEX IF NOT EXISTS idx_x402_tool ON x402_receipts(tool);
CREATE INDEX IF NOT EXISTS idx_x402_agent ON x402_receipts(agent_id);

-- News comments + reactions (NEWS-PAGE-DESIGN.md §3)
CREATE TABLE IF NOT EXISTS news_reactions (
    news_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    reaction   TEXT NOT NULL CHECK (reaction IN ('like', 'dislike')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (news_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_news_reactions_news ON news_reactions(news_id);
CREATE INDEX IF NOT EXISTS idx_news_reactions_user ON news_reactions(user_id);

CREATE TABLE IF NOT EXISTS news_comments (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    news_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    parent_id  UUID REFERENCES news_comments(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    edited_at  TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_news_comments_news ON news_comments(news_id);
CREATE INDEX IF NOT EXISTS idx_news_comments_parent ON news_comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_news_comments_user ON news_comments(user_id);
CREATE INDEX IF NOT EXISTS idx_news_comments_created ON news_comments(created_at DESC);

CREATE TABLE IF NOT EXISTS news_comment_reactions (
    comment_id UUID NOT NULL REFERENCES news_comments(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    reaction   TEXT NOT NULL CHECK (reaction IN ('like', 'dislike')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (comment_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_news_comment_reactions_comment ON news_comment_reactions(comment_id);
"""


async def main() -> None:
    """Run the schema migration."""
    cfg = {
        "host": os.getenv("POSTGRES_HOST", "rmi-postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "user": os.getenv("POSTGRES_USER", "rmi"),
        "password": os.getenv(
            "POSTGRES_PASSWORD", "d01258dbfa59738b5e442f4f2df48721d934522276cdf5b7ae78d7e38041c0f4"
        ),  # Rotated 2026-07-17
        "database": os.getenv("POSTGRES_DB", "rmi"),
    }
    logger.info(f"Connecting to postgres at {cfg['host']}:{cfg['port']} db={cfg['database']}")
    conn = await asyncpg.connect(**cfg)
    try:
        await conn.execute(SCHEMA)
        logger.info("✓ Schema applied")
        # Verify
        tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        logger.info(f"Tables ({len(tables)}):")
        for t in tables:
            logger.info(f"  {t['tablename']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
