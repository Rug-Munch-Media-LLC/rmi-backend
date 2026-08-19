"""Embedding utilities — delegates to rugbrain HTTP API."""


class RAGUnavailableError(Exception):
    """RAG system is unavailable."""

    pass


async def get_embedding(text: str, model: str = "") -> list[float]:
    """Get embedding vector via rugbrain POST /api/v1/embed (Qwen3-4B)."""
    import os

    import httpx

    api_url = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1")
    api_key = os.getenv("RMI_RAG_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{api_url}/embed",
                json={"text": text[:512]},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json().get("embedding", [])
    except Exception:
        pass
    return []
