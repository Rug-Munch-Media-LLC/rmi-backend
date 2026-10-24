"""LiteLLM (OpenAI-compatible) upstream client.

This is the only module that knows the provider base URL. The browser never
receives it, and the optional upstream key stays server-side.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any, Final

import httpx

DEFAULT_BASE: Final = "http://localhost:4000/v1"
DEFAULT_TIMEOUT: Final = 90.0


class UpstreamError(Exception):
    """Upstream failure, carrying the status for the gateway to map."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"upstream {status_code}: {detail}")


class LiteLLMProxy:
    """Non-streaming and streaming calls to LiteLLM's chat completions."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("LITELLM_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or os.getenv("LITELLM_API_KEY") or ""
        self.timeout = timeout
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One chat completion. Raises :class:`UpstreamError` on failure."""
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(502, f"transport_error: {type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            raise UpstreamError(response.status_code, response.text[:300])
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(502, "invalid_json") from exc
        if not isinstance(body, dict):
            raise UpstreamError(502, "invalid_payload")
        return body

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Pass SSE bytes through unchanged, after a status check."""
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={**self._headers(), "Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    raise UpstreamError(response.status_code, body)
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise UpstreamError(502, f"transport_error: {type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()


def default_proxy() -> LiteLLMProxy:
    """Construct the production proxy from settings/env."""
    try:
        from app.config import get_settings

        base = get_settings().litellm_api_base
    except (ImportError, AttributeError):  # pragma: no cover - config optional
        base = None
    return LiteLLMProxy(base_url=base)
