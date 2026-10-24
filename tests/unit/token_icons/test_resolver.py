"""Resolver behaviour against mocked transports (no live network)."""

from __future__ import annotations

import httpx
import pytest

from app.token_icons.fallback import fallback_icon_url
from app.token_icons.resolver import resolve_icon
from tests.unit.token_icons.conftest import (
    dexscreener_handler,
    image_response,
    make_client,
)

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"


def _handler(image_url: str | None):
    base = dexscreener_handler(CHAIN, image_url)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://dd.dexscreener.com") or str(
            request.url
        ).startswith("https://assets.coingecko.com"):
            return image_response(str(request.url))
        return base(request)

    return handler


@pytest.mark.asyncio
async def test_resolves_and_caches_after_probe(memory_cache) -> None:
    client = make_client(_handler("https://dd.dexscreener.com/ds-data/tokens/solana/x.png"))
    first = await resolve_icon(CHAIN, TOKEN, client=client, cache=memory_cache)
    assert first["icon_url"] == "https://dd.dexscreener.com/ds-data/tokens/solana/x.png"
    assert first["source"] == "dexscreener"
    assert first["fallback"] is False
    assert first["content_type"] == "image/png"

    second = await resolve_icon(CHAIN, TOKEN, client=make_client(_handler("https://evil.example")), cache=memory_cache)
    assert second["icon_url"] == first["icon_url"]
    assert second["cached"] is True
    await client.aclose()


@pytest.mark.asyncio
async def test_rejects_html_content_type_and_falls_back(memory_cache) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/latest/dex/tokens/" in str(request.url):
            return httpx.Response(
                200, json={"pairs": [{"chainId": CHAIN, "info": {"imageUrl": "https://cdn.example/x.png"}}]}
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    client = make_client(handler)
    result = await resolve_icon(CHAIN, TOKEN, client=client, cache=memory_cache)
    assert result["fallback"] is True
    assert result["icon_url"] == fallback_icon_url(CHAIN, TOKEN)
    assert f"{CHAIN}:{TOKEN}" in memory_cache.missing
    await client.aclose()


@pytest.mark.asyncio
async def test_svg_candidate_is_never_accepted(memory_cache) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/latest/dex/tokens/" in str(request.url):
            return httpx.Response(
                200, json={"pairs": [{"chainId": CHAIN, "info": {"imageUrl": "https://cdn.example/x.svg"}}]}
            )
        return httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>")

    client = make_client(handler)
    result = await resolve_icon(CHAIN, TOKEN, client=client, cache=memory_cache)
    assert result["fallback"] is True
    await client.aclose()


@pytest.mark.asyncio
async def test_404_upstream_uses_negative_cache(memory_cache) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/latest/dex/tokens/" in str(request.url):
            return httpx.Response(200, json={"pairs": [{"chainId": CHAIN, "info": {"imageUrl": "https://cdn.example/x.png"}}]})
        return httpx.Response(404)

    client = make_client(handler)
    first = await resolve_icon(CHAIN, TOKEN, client=client, cache=memory_cache)
    assert first["fallback"] is True
    before = len(calls)
    second = await resolve_icon(CHAIN, TOKEN, client=client, cache=memory_cache)
    assert second["fallback"] is True
    assert len(calls) == before, "negative cache must stop further upstream calls"
    await client.aclose()


@pytest.mark.asyncio
async def test_empty_token_never_hits_network(memory_cache) -> None:
    client = make_client(lambda request: pytest.fail("no upstream call expected"))
    result = await resolve_icon("", "", client=client, cache=memory_cache)
    assert result["fallback"] is True
    await client.aclose()


@pytest.mark.asyncio
async def test_broken_provider_does_not_block_next_source(memory_cache) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/latest/dex/tokens/" in str(request.url):
            return httpx.Response(500)
        if str(request.url).startswith("https://api.coingecko.com"):
            return httpx.Response(
                200, json={"image": {"large": "https://assets.coingecko.com/coins/images/1/large/eth.png"}}
            )
        return image_response(str(request.url))

    client = make_client(handler)
    result = await resolve_icon("ethereum", "0xdAC17F958D2ee523a2206206994597C13D831ec7", client=client, cache=memory_cache)
    assert result["source"] == "coingecko"
    assert result["fallback"] is False
    await client.aclose()
