#!/usr/bin/env python3
"""
Supabase vector search helper (RAG integration).
Uses the existing search_embeddings RPC already installed on the project.
No pinecone/weaviate needed - SQL + pgvector.
"""

import os
from typing import Any

import httpx

from app.core.secrets import get_secret_or_none


def _get_url() -> Any | None:
    return os.environ.get("SUPABASE_URL", "")


def _get_key() -> Any:
    return (
        get_secret_or_none("SUPABASE_SERVICE_ROLE_KEY")
        or get_secret_or_none("SUPABASE_SERVICE_KEY", "rmi/prod/supabase_service_key")
        or ""
    )


def _get_headers() -> dict:
    key = _get_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def search_similar(
    query_embedding: list[float],
    namespace: str = "default",
    match_count: int = 10,
    similarity_threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """
    Search for semantically similar documents using pgvector.
    Returns matching document IDs with similarity scores.

    Args:
        query_embedding: The embedding vector from your model
        namespace: Search namespace to restrict results
        match_count: Number of results to return
        similarity_threshold: Minimum cosine similarity (0-1)
    """
    # Pad/truncate to match the pgvector table dimension
    from app.supabase_vector import TABLE_DIM, pad_vector

    padded_embedding = pad_vector(query_embedding, TABLE_DIM)

    url = f"{_get_url()}/rest/v1/rpc/search_embeddings"
    payload = {
        "query_embedding": padded_embedding,
        "match_count": match_count,
        "namespace": namespace,
        "similarity_threshold": similarity_threshold,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload, headers=_get_headers())
        if r.status_code == 200:
            return r.json()
        return []


async def store_embedding(
    document_id: str,
    embedding: list[float],
    namespace: str = "default",
    content_hash: str = "",
    metadata: dict | None = None,
    model_name: str = "",
) -> dict | None:
    """
    Store an embedding for later retrieval.
    Idempotent - uses ON CONFLICT (document_id) for upsert via REST.
    """
    url = f"{_get_url()}/rest/v1/embeddings"
    payload = {
        "document_id": document_id,
        "embedding": embedding,
        "namespace": namespace,
        "content_hash": content_hash,
        "metadata": metadata or {},
        "model_name": model_name,
    }
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload, headers=headers)
        return r.json() if r.status_code in (200, 201) else None


async def get_namespace_stats(namespace: str = "default") -> dict:
    """Get document count and stats for a namespace."""
    url = f"{_get_url()}/rest/v1/embeddings?namespace=eq.{namespace}&select=id"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers={**_get_headers(), "Prefer": "count=exact"})
        return {
            "namespace": namespace,
            "count": int(r.headers.get("content-range", "0").split("/")[-1] or 0),
            "status": r.status_code,
        }
