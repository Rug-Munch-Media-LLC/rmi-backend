-- ═════════════════════════════════════════════════════════════════════
-- RMI pgvector Setup — Run ONCE in Supabase SQL Editor
-- https://<your-project>.supabase.co → SQL Editor
--
-- IMPORTANT: The vector dimension MUST match RAG_EMBEDDING_DIM in .env
-- Currently set to 640 (local BGE-small 384 + code 128 + behavioral 64 + wallet 64)
-- ═════════════════════════════════════════════════════════════════════

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Drop existing table if dimension mismatch (DESTROYS EXISTING DATA — backup first!)
-- Uncomment only if you need to change the vector dimension:
-- DROP TABLE IF EXISTS rag_vectors CASCADE;

-- 3. Create the vector table
CREATE TABLE IF NOT EXISTS rag_vectors (
    id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    content TEXT,
    embedding vector(640),
    metadata JSONB DEFAULT '{}',
    source TEXT,
    severity TEXT DEFAULT 'medium',
    chain TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create indexes
CREATE INDEX IF NOT EXISTS idx_rag_collection ON rag_vectors (collection);
CREATE INDEX IF NOT EXISTS idx_rag_severity ON rag_vectors (severity);
CREATE INDEX IF NOT EXISTS idx_rag_source ON rag_vectors (source);
CREATE INDEX IF NOT EXISTS idx_rag_chain ON rag_vectors (chain);
CREATE INDEX IF NOT EXISTS idx_rag_content_fts ON rag_vectors
    USING GIN (to_tsvector('english', COALESCE(content, '')));

-- 5. Create or replace the store_embedding function
CREATE OR REPLACE FUNCTION store_embedding(
    document_id TEXT,
    embedding vector(640),
    namespace TEXT DEFAULT 'default',
    content_hash TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    model_name TEXT DEFAULT ''
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    result JSONB;
BEGIN
    INSERT INTO rag_vectors (id, collection, embedding, metadata, source, updated_at)
    VALUES (document_id, namespace, embedding, metadata, model_name, NOW())
    ON CONFLICT (id) DO UPDATE SET
        embedding = EXCLUDED.embedding,
        metadata = EXCLUDED.metadata,
        updated_at = NOW();

    result = jsonb_build_object('status', 'stored', 'document_id', document_id);
    RETURN result;
END;
$$;

-- 6. Create or replace the search_embeddings RPC (640-dim — primary)
CREATE OR REPLACE FUNCTION search_embeddings(
    query_embedding vector(640),
    namespace TEXT DEFAULT 'default',
    match_count INT DEFAULT 10,
    similarity_threshold FLOAT DEFAULT 0.7
) RETURNS TABLE (
    id TEXT,
    content TEXT,
    metadata JSONB,
    source TEXT,
    severity TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        rv.id,
        rv.content,
        rv.metadata,
        rv.source,
        rv.severity,
        (1 - (rv.embedding <=> query_embedding))::FLOAT AS similarity
    FROM rag_vectors rv
    WHERE (namespace = 'default' OR rv.collection = namespace)
      AND 1 - (rv.embedding <=> query_embedding) > similarity_threshold
    ORDER BY rv.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 7. Create a compatibility RPC for smaller vectors (padded to 640 by the app)
-- This is a copy that accepts the same 640-dim but is named for clarity
-- (the app pads all vectors to 640 before calling search_embeddings)

-- 8. After data is loaded, build the ANN index:
-- Run this separately after loading data:
-- CREATE INDEX IF NOT EXISTS idx_rag_embedding_hnsw ON rag_vectors
--     USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);

-- Verify
SELECT 'pgvector setup complete (vector(640))' AS status;
SELECT count(*) AS vector_count FROM rag_vectors;
