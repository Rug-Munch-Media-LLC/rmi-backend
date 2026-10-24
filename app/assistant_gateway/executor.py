"""Server-side tool execution against this backend's own endpoints.

The executor is the only component that talks to tools. It holds the internal
key (never exposed to the browser) and validates every id-like argument before
building a URL, so a model-supplied string cannot become a path traversal or an
arbitrary outbound request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.assistant_gateway.tools import ToolResult

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9:_\-]{1,128}$")
_SAFE_CHAIN: Final = re.compile(r"^[a-z0-9\-]{1,32}$")

_ROUTES: Final[dict[str, tuple[str, str]]] = {
    "scanner_risk": ("GET", "/api/v2/scanner/token/risk/{chain}/{address}"),
    "scanner_full": ("GET", "/api/v2/scanner/token/full/{chain}/{address}"),
    "scanner_price": ("GET", "/api/v2/scanner/token/price/{chain}/{address}"),
    "scanner_holders": ("GET", "/api/v2/scanner/token/holders/{chain}/{address}"),
    "trades_recent": ("GET", "/api/v1/trades/{chain}/{address}"),
    "trades_stats": ("GET", "/api/v1/trades/{chain}/{address}/stats"),
    "rugmaps_cluster_analysis": ("POST", "/api/v1/rugmaps/cluster-analysis"),
    "rag_search": ("POST", "/api/v1/rag/v2/ask"),
}


def _safe(value: Any, pattern: re.Pattern[str], default: str = "") -> str:
    """Validate an id-like argument; invalid input becomes the empty string."""
    text = str(value or "").strip()
    return text if pattern.match(text) else default


@dataclass
class InternalApiToolExecutor:
    """Executes grantable tools over loopback HTTP with the internal key."""

    base_url: str = "http://127.0.0.1:8000"
    internal_key: str | None = None
    timeout: float = 20.0
    routes: dict[str, tuple[str, str]] = field(default_factory=lambda: dict(_ROUTES))

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Dispatch one tool call. Validation failures never touch the network."""
        route = self.routes.get(name)
        if route is None:
            return ToolResult(ok=False, error="tool_not_granted")
        method, template = route
        chain = _safe(arguments.get("chain"), _SAFE_CHAIN, "solana")
        address = _safe(arguments.get("address") or arguments.get("token"), _SAFE_ID)
        params: dict[str, Any] | None = None
        body: dict[str, Any] | None = None
        if name == "rag_search":
            query = str(arguments.get("query") or "").strip()[:500]
            if not query:
                return ToolResult(ok=False, error="invalid_arguments")
            body = {"query": query}
        elif name == "rugmaps_cluster_analysis":
            cluster = _safe(arguments.get("cluster"), _SAFE_ID)
            if not cluster:
                return ToolResult(ok=False, error="invalid_arguments")
            body = {"chain": chain, "cluster": cluster}
        else:
            if not address:
                return ToolResult(ok=False, error="invalid_arguments")
            if name == "trades_recent":
                limit = int(arguments.get("limit", 20) or 20)
                params = {"limit": max(1, min(limit, 100))}
        path = template.format(chain=chain, address=address)
        return await self._call(method, path, params=params, body=body, name=name)

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        name: str,
    ) -> ToolResult:
        """Perform the loopback request and normalise the payload."""
        import httpx  # local import keeps module import cheap

        headers = {"Accept": "application/json"}
        if self.internal_key:
            headers["X-Internal-Key"] = self.internal_key
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(
                    method,
                    f"{self.base_url.rstrip('/')}{path}",
                    params=params,
                    json=body,
                    headers=headers,
                )
        except httpx.HTTPError:
            return ToolResult(ok=False, error="tool_unavailable")
        if resp.status_code >= 400:
            return ToolResult(ok=False, error=f"upstream_{resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            return ToolResult(ok=False, error="invalid_response")
        if name == "rag_search":
            return _rag_result(payload)
        return ToolResult(ok=True, data=payload)


def _rag_result(payload: Any) -> ToolResult:
    """Normalise a RAG answer into a ToolResult carrying citations."""
    if not isinstance(payload, dict):
        return ToolResult(ok=False, error="invalid_response")
    if payload.get("verified") is False:
        return ToolResult(ok=False, error="unverified_sources")
    sources = payload.get("sources") or []
    citations: list[dict[str, str]] = []
    for source in sources:
        if isinstance(source, str):
            citations.append({"title": source[:200], "url": "", "snippet": ""})
        elif isinstance(source, dict):
            citations.append(
                {
                    "title": str(source.get("title") or source.get("name") or "")[:200],
                    "url": str(source.get("url") or "")[:500],
                    "snippet": str(source.get("snippet") or source.get("text") or "")[:300],
                }
            )
    return ToolResult(
        ok=bool(citations),
        data=payload.get("answer", ""),
        error="" if citations else "no_sources",
        citations=tuple(citations),
    )
