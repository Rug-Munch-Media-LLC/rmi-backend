"""Single-channel ingestion: paginate the public preview, classify, persist.

Backfill is bounded (``limit`` posts) and stateful: a channel's ``last_id`` in
``<channel>.state.json`` stops pagination as soon as known history is reached,
so a repeat run reads one page and appends only genuinely new posts.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.tg_news import classify, state, store
from app.databus.tg_news.fetch import fetch_preview
from app.databus.tg_news.parse import parse_channel_page

logger = get_logger("tg_news")

MAX_PAGES = 12
PAGE_DELAY = 0.7


async def ingest_channel(
    client: httpx.AsyncClient,
    channel: str,
    *,
    limit: int = 200,
    kind_hint: str | None = None,
    delay: float = PAGE_DELAY,
    root: str | None = None,
) -> dict[str, Any]:
    """Ingest one channel and return a per-channel run summary."""
    handle = channel.lstrip("@")
    prior = state.load_state(handle, root)
    last_id = int(prior["last_id"]) if prior.get("last_id") else None
    collected: list[dict[str, Any]] = []
    meta = None
    etag: str | None = None
    before: int | None = None
    requests = 0
    caught_up = False

    try:
        while requests < MAX_PAGES and len(collected) < limit:
            response = await fetch_preview(client, handle, before=before, etag=etag if before is None else None)
            requests += 1
            if response.not_modified:
                break
            etag = response.etag or etag
            page = parse_channel_page(response.html or "", handle)
            meta = meta or page
            if not page.posts:
                break
            ids = [post["message_id"] for post in page.posts]
            collected = page.posts + collected
            if last_id is not None and min(ids) <= last_id:
                caught_up = True
                break
            before = min(ids)
            if len(page.posts) < 10:
                break
            await asyncio.sleep(delay)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("telegram preview ingest failed for %s: %s", handle, exc)
        return {
            "channel": handle,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "fetched": len(collected),
            "requests": requests,
        }

    collected = collected[-limit:]
    fresh = [post for post in collected if last_id is None or post["message_id"] > last_id]
    kind = kind_hint or prior.get("profile", {}).get("kind") or prior.get("kind")
    annotated = classify.annotate(fresh, kind)
    written = store.append_posts(handle, annotated, root)
    window = annotated or classify.annotate(collected, kind)
    profile = classify.channel_profile(window, kind)

    newest = max((post["message_id"] for post in collected), default=last_id or 0)
    state.save_state(
        handle,
        {
            **prior,
            "channel": handle,
            "last_id": max(newest, last_id or 0),
            "etag": etag or prior.get("etag"),
            "title": (meta.title if meta else None) or prior.get("title"),
            "subscribers": (meta.subscribers if meta else None) or prior.get("subscribers"),
            "description": (meta.description if meta else None) or prior.get("description"),
            "profile": profile,
            "kind": profile["kind"],
            "trust_weight": profile["trust_weight"],
            "last_run": datetime.now(UTC).isoformat(),
            "last_run_fetched": len(collected),
            "last_run_new": len(fresh),
            "items_total": int(prior.get("items_total", 0)) + written["appended"],
        },
        root,
    )
    return {
        "channel": handle,
        "requests": requests,
        "fetched": len(collected),
        "new": len(fresh),
        "appended": written["appended"],
        "duplicates_skipped": written["skipped"],
        "caught_up": caught_up,
        "last_id": max(newest, last_id or 0),
        "subscribers": meta.subscribers if meta else prior.get("subscribers"),
        "profile": profile,
        "kind": profile["kind"],
        "trust_weight": profile["trust_weight"],
    }
