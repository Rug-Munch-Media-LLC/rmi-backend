"""Embedding Router - thin wrapper around smart rate-limited dispatcher."""

from __future__ import annotations

from typing import Any

from app.rate_limiter import get_dispatcher


async def get_router() -> Any:
    return await get_dispatcher()


async def embed_texts(texts, task="default") -> Any:
    return await (await get_dispatcher()).embed(texts, task)


async def embed_single(text, task="default") -> tuple[Any, Any]:
    vecs, provider = await (await get_dispatcher()).embed([text], task)
    return vecs[0] if vecs else [], provider
