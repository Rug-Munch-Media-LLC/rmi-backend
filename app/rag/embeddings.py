"""Embedding utilities — minimal stub preserved for backward compatibility.

The primary embedding pipeline now lives in rugbrain (rmi-rag).
These stubs maintain import compatibility for callers that haven't
migrated to ``app.integrations.rugbrain_client``.
"""

import asyncio


# Preserved for callers that catch this for graceful degradation
class RAGUnavailableError(Exception):
    """RAG system is unavailable."""

    pass


async def get_embedding(text: str, model: str = "") -> list[float]:
    """Get embedding vector — delegates to rugbrain HTTP API."""
    import httpx
    from app.config import settings

    # Call rugbrain's embed endpoint if available
    url = f"{settings.get('RAG_API_URL', 'http://rugbrain-api:8012/api/v1')}/embed"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                url,
                json={"text": text},
                headers={"X-API-Key": settings.get("RMI_RAG_API_KEY", "")},
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("embedding", [])
    except Exception:
        pass
    # Fallback: return empty vector
    return []
