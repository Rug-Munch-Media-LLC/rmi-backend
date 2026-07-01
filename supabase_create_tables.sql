-- ============================================================
-- Create missing Supabase tables for RMI backend
-- Tables: rag_vectors, connected_accounts, notifications
-- ============================================================

-- Enable pgvector extension (required for rag_vectors)
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1. rag_vectors — Core pgvector table for RAG vector search
-- Schema from: /root/backend/app/supabase_vector.py
-- ============================================================
CREATE TABLE IF NOT EXISTS rag_vectors (
    id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    content TEXT,
    embedding vector(384),
    metadata JSONB DEFAULT '{}',
    source TEXT,
    severity TEXT,
    chain TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for rag_vectors
CREATE INDEX IF NOT EXISTS idx_rag_collection ON rag_vectors (collection);
CREATE INDEX IF NOT EXISTS idx_rag_metadata ON rag_vectors USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_rag_severity ON rag_vectors (severity);
CREATE INDEX IF NOT EXISTS idx_rag_chain ON rag_vectors (chain);
CREATE INDEX IF NOT EXISTS idx_rag_source ON rag_vectors (source);
CREATE INDEX IF NOT EXISTS idx_rag_content_fts ON rag_vectors
    USING GIN (to_tsvector('english', COALESCE(content, '')));

-- ============================================================
-- 2. connected_accounts — Linked social/wallet accounts
-- Schema from: /root/backend/app/routers/profile_router.py
-- Used columns: user_id, account_type, account_id (provider_user_id),
--   account_name, is_verified, verified_at + metadata
-- ============================================================
CREATE TABLE IF NOT EXISTS connected_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    account_type TEXT,
    provider_user_id TEXT,
    account_name TEXT,
    is_verified BOOLEAN DEFAULT FALSE,
    verified_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connected_accounts_user_id ON connected_accounts (user_id);
CREATE INDEX IF NOT EXISTS idx_connected_accounts_type ON connected_accounts (account_type);

-- ============================================================
-- 3. notifications — User notifications
-- Schema from: /root/backend/app/routers/profile_router.py
-- Used columns: user_id, notification_type, title, message,
--   data, is_read, read_at + related_user_id
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    type TEXT,
    title TEXT,
    message TEXT,
    related_user_id UUID,
    data JSONB DEFAULT '{}',
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications (user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications (is_read);
