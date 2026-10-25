"""Supabase content: tokens, bulletin board, badges."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.services.supabase_transport import (
    _get,
    _get_headers,
    _get_in,
    _patch,
    _post,
    _url,
)


async def get_tokens(chain: str | None = None, limit: int = 50, order_by: str = "volume_24h.desc") -> list[Any]:
    params = {}
    if chain:
        params["chain"] = chain
    return await _get("tokens", params, order=order_by, limit=limit)


async def get_token(address: str, chain: str = "ethereum") -> dict[str, Any] | None:
    data = await _get("tokens", {"address": address, "chain": chain}, limit=1)
    return data[0] if data else None


async def upsert_token(address: str, chain: str, **fields) -> dict[str, Any] | None:
    data = {
        "address": address,
        "chain": chain,
        "last_fetched": datetime.now(UTC).isoformat(),
        **fields,
    }
    url = f"{_url('tokens')}"
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        return r.json()[0] if r.status_code in (200, 201) and r.json() else None


async def get_bulletin_posts(category: str | None = None, sort: str = "created_at.desc", limit: int = 20) -> list[Any]:
    params = {}
    if category and category != "all":
        params["category"] = category
    return await _get("bulletin_posts", params, order=sort, limit=limit)


async def create_bulletin_post(
    title: str,
    content: str,
    author_id: str,
    category: str,
    chain: str | None = None,
    tags: list[Any] | None = None,
    risk_score: int | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "title": title,
        "content": content,
        "author_id": author_id,
        "category": category,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if chain:
        data["chain"] = chain
    if tags:
        data["tags"] = tags
    if risk_score:
        data["risk_score"] = risk_score
    if source:
        data["source"] = source

    return await _post("bulletin_posts", data)


async def vote_post(post_id: int, vote_type: str) -> bool:
    """vote_type: 'up' or 'down'"""
    field = "upvotes" if vote_type == "up" else "downvotes"
    # Get current value
    posts = await _get("bulletin_posts", {"id": post_id}, select=f"id,{field}", limit=1)
    if not posts:
        return False
    current = posts[0].get(field, 0) or 0
    return await _patch("bulletin_posts", {"id": post_id}, {field: current + 1})


async def get_badges(rarity: str | None = None) -> list[Any]:
    params = {}
    if rarity:
        params["rarity"] = rarity
    return await _get("badges", params, order="rarity.asc")


async def get_user_badges(user_id: str) -> list[Any]:
    """Get user's earned badges with full badge details."""
    user_badges = await _get("user_badges", {"user_id": user_id})
    if not user_badges:
        return []

    badge_ids = [ub["badge_id"] for ub in user_badges]
    badges = await _get_in("badges", "id", badge_ids)
    badge_map = {b["id"]: b for b in badges}

    result = []
    for ub in user_badges:
        badge = badge_map.get(ub["badge_id"])
        if badge:
            result.append({**ub, "badge_detail": badge})
    return result


async def award_badge(user_id: str, badge_id: str, earned_via: str = "auto") -> dict[str, Any] | None:
    return await _post(
        "user_badges",
        {
            "user_id": user_id,
            "badge_id": badge_id,
            "earned_at": datetime.now(UTC).isoformat(),
            "earned_via": earned_via,
        },
    )
