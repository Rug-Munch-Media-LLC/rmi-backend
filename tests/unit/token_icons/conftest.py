"""Shared fixtures — memory cache + recording MockTransport (no network)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


class MemoryCache:
    """In-memory stand-in for :class:`app.token_icons.cache.IconCache`."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}
        self.missing: set[str] = set()

    async def get(self, chain: str, token: str) -> dict[str, Any] | None:
        return self.entries.get(f"{chain}:{token}")

    async def put(self, chain: str, token: str, payload: dict[str, Any]) -> None:
        self.entries[f"{chain}:{token}"] = payload

    async def is_missing(self, chain: str, token: str) -> bool:
        return f"{chain}:{token}" in self.missing

    async def mark_missing(self, chain: str, token: str) -> None:
        self.missing.add(f"{chain}:{token}")


def image_response(url: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)


def dexscreener_handler(chain_id: str, image_url: str | None):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/latest/dex/tokens/" in str(request.url):
            pairs = [{"chainId": chain_id, "info": {"imageUrl": image_url}}] if image_url else []
            return httpx.Response(200, json={"pairs": pairs})
        return httpx.Response(404)

    return handler


@pytest.fixture
def memory_cache() -> MemoryCache:
    return MemoryCache()


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_payload(value: dict[str, Any]) -> str:
    return json.dumps(value)
