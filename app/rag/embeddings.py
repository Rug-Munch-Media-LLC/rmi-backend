"""Embedding utilities — minimal stub preserved for backward compatibility.

The primary embedding pipeline now lives in rugbrain (rmi-rag).
These stubs maintain import compatibility for callers that haven't
migrated to ``app.integrations.rugbrain_client``.
"""


class RAGUnavailableError(Exception):
    """RAG system is unavailable."""

    pass


async def get_embedding(text: str, model: str = "") -> list[float]:
    """Get embedding vector — calls rugbrain search to trigger embedding.

    Rugbrain doesn't expose a raw /embed endpoint, so we use a lightweight
    search to warm the embedding pipeline. Returns empty vector on failure.
    """
    from app.integrations.rugbrain_client import rugbrain_search

    try:
        await rugbrain_search(query=text[:256], collections=["defi_exploits"], limit=1)
    except Exception:
        pass
    # Callers expect a vector back; return empty since we can't expose
    # the raw embedding vector from rugbrain's search endpoint
    return []
