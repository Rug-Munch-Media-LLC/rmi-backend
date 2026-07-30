"""RMI SDK - Python client for Rug Munch Intelligence.

v4.0 Sovereign-first. Friendly wrapper around the auto-generated
OpenAPI client. See rugmunch/ for the full generated SDK.

Quick start:
    >>> from rugmunch_sdk import RMI
    >>> client = RMI(base_url="https://api.rugmunch.io", api_key="...")
    >>> risk = client.get_token_risk(chain="solana", address="DezXAZ...")
    >>> print(risk.tier, risk.score)
    >>>
    >>> report = client.generate_report("token", "solana:DezXAZ...")
    >>> print(report.markdown[:500])
"""

from __future__ import annotations

import asyncio

import httpx

from .models import (
    McpTool,
    NewsItem,
    RagHit,
    Report,
    TokenRisk,
)


class RMI:
    """Async Python client for the Rug Munch Intelligence API."""

    def __init__(
        self,
        base_url: str = "https://api.rugmunch.io",
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"X-Agent-Id": api_key} if api_key else {},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        await self._client.aclose()

    async def _get(self, path: str, **params) -> dict:
        r = await self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, json: dict | None = None) -> dict:
        r = await self._client.post(path, json=json or {})
        r.raise_for_status()
        return r.json()

    # ── Health / introspection ──────────────────────────────────
    async def get_health(self) -> dict:
        return await self._get("/health")

    async def get_catalog_stats(self) -> dict:
        return await self._get("/api/v1/catalog/stats")

    async def get_x402_catalog(self) -> list[dict]:
        d = await self._get("/api/v1/x402/catalog")
        return d.get("tools", [])

    # ── MCP tools ───────────────────────────────────────────────
    async def list_mcp_tools(self) -> list[McpTool]:
        d = await self._get("/mcp/tools")
        return [McpTool(**t) for t in d.get("tools", [])]

    async def call_mcp_tool(self, name: str, arguments: dict | None = None) -> dict:
        return await self._post(f"/mcp/call/{name}", json={"arguments": arguments or {}})

    # ── Catalog (the v4.0 moat) ─────────────────────────────────
    async def get_token_risk(self, chain: str, address: str) -> TokenRisk:
        d = await self._get(f"/api/v1/catalog/tokens/{chain}/{address}/risk")
        return TokenRisk(**d)

    async def get_wallet_analysis(self, chain: str, address: str) -> dict:
        return await self._get(f"/api/v1/catalog/wallets/{chain}/{address}")

    async def resolve_entity(self, wallet_id: str, max_chains: int = 5) -> dict:
        return await self._post(
            "/api/v1/catalog/entities/resolve",
            json={"wallet_id": wallet_id, "max_chains": max_chains},
        )

    # ── RAG ──────────────────────────────────────────────────────
    async def rag_search(self, query: str, collection: str = "scam_intel", top_k: int = 5) -> list[RagHit]:
        d = await self._post(
            "/api/v1/rag/v2/search",
            json={"query": query, "collection": collection, "top_k": top_k},
        )
        return [RagHit(**h) for h in d.get("hits", [])]

    async def rag_ingest(self, content: str, collection: str = "scam_intel", doc_id: str | None = None) -> dict:
        return await self._post(
            "/api/v1/rag/v2/ingest",
            json={"content": content, "collection": collection, "doc_id": doc_id},
        )

    # ── News (T28) ──────────────────────────────────────────────
    async def get_news_trending(self, window_hours: int = 24, limit: int = 20) -> list[NewsItem]:
        d = await self._get("/api/v1/news/trending", window_hours=window_hours, limit=limit)
        return [NewsItem(**i) for i in d.get("items", [])]

    async def list_news(self, since_hours: int = 24, limit: int = 20) -> list[NewsItem]:
        d = await self._post(
            "/api/v1/news",
            json={"since_hours": since_hours, "limit": limit},
        )
        return [NewsItem(**i) for i in d.get("items", [])]

    async def get_news(self, news_id: str) -> NewsItem:
        d = await self._get(f"/api/v1/news/{news_id}")
        return NewsItem(**d)

    async def analyze_news(self, news_id: str) -> str | None:
        d = await self._post(f"/api/v1/news/{news_id}/analyze")
        return d.get("analysis")

    # ── Reports (T29) ───────────────────────────────────────────
    async def generate_report(self, subject_type: str, subject_id: str, save: bool = True) -> Report:
        d = await self._post(
            "/api/v1/reports/generate",
            json={"subject_type": subject_type, "subject_id": subject_id, "save": save},
        )
        return Report(**d)


# ── Sync convenience (for scripts / notebooks) ────────────────────
class SyncRMI:
    """Sync wrapper. Uses a private event loop per call."""

    def __init__(self, *args, **kwargs):
        self._client = RMI(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        asyncio.run(self._client.close())

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        def sync_call(*a, **kw):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(attr(*a, **kw))
            finally:
                loop.close()

        return sync_call


def RMI_sync(*args, **kwargs) -> SyncRMI:
    """Shortcut to construct a SyncRMI."""
    return SyncRMI(*args, **kwargs)
