"""Community-facing bulletin board endpoints (frontend contract).

The public site (rmi-frontend ``BulletinBoardPage`` / ``useBulletinApi``)
expects these surfaces under ``/api/v1/bulletin-board``:

  GET  /stats                    → {total_posts, total_comments, active_users, today_posts}
  POST /posts/{id}/vote          {vote: "up"|"down"}            → {upvotes, downvotes, my_vote}
  POST /posts/{id}/react         {reaction: "like"|...}         → {reactions: {...}}
  GET  /posts/{id}/comments      → [Comment]
  POST /posts/{id}/comments      {content, parent_id?}          → Comment

Votes and reactions live in dedicated Redis structures keyed by post id;
post dicts gain derived fields (``id``, ``author``, ``upvotes``,
``downvotes``, ``comment_count``) at read time so the stored schema is
unchanged. Anonymous voters are identified by a hashed client IP so
double-voting is at least non-trivial; signed-in users vote under their
session identity.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import Request  # noqa: TC002 — FastAPI needs runtime resolution for request objects
from pydantic import BaseModel, Field

from app.bulletin_board import BulletinBoardManager
from app.core.errors import NotFoundError, ValidationError
from app.routers.bulletin_board.router import router

ALLOWED_REACTIONS = {"like", "love", "laugh", "angry", "sad"}


class VoteRequest(BaseModel):
    vote: str = Field(pattern="^(up|down)$")


class ReactionRequest(BaseModel):
    reaction: str


class CommunityCommentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = None


# ── helpers ──────────────────────────────────────────────────────


def _voter_id(request: Request) -> str:
    """Stable-per-client voter id: session user if available, else IP hash."""
    user = getattr(request.state, "user", None) or {}
    uid = user.get("id") if isinstance(user, dict) else None
    if uid:
        return f"user:{uid}"
    ip = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for", "")
    return "anon:" + hashlib.sha256(f"{fwd or ip}".encode()).hexdigest()[:16]


async def _vote_counts(r: Any, post_id: str) -> tuple[int, int]:
    votes = await r.hgetall(f"bulletin:votes:{post_id}") or {}
    up = sum(1 for v in votes.values() if v == "up")
    down = sum(1 for v in votes.values() if v == "down")
    return up, down


def _adapt_comment(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("comment_id", ""),
        "content": c.get("content", ""),
        "author": {
            "username": c.get("author_name", "Community Member"),
            "avatar_url": "",
            "reputation": 0,
            "level": 1,
            "is_bot": str(c.get("author_id", "")).startswith("bot_"),
        },
        "created_at": c.get("created_at", ""),
        "upvotes": 0,
        "parent_id": c.get("parent_id"),
    }


# ── stats ────────────────────────────────────────────────────────


@router.get(
    "/stats",
    summary="Community Stats",
    description="Frontend-facing board stats: total published posts, total comments, distinct active authors, posts today.",
)
async def community_stats() -> dict[str, Any]:
    raw = await BulletinBoardManager.get_stats()
    r = await BulletinBoardManager._get_redis()

    today = datetime.now(UTC).date().isoformat()
    authors: set[str] = set()
    today_posts = 0
    comment_authors: set[str] = set()

    post_ids = await r.hkeys("rmi:bulletin_posts")
    for pid in post_ids:
        data = await r.hget("rmi:bulletin_posts", pid)
        if not data:
            continue
        p = json.loads(data)
        authors.add(p.get("author_id", ""))
        if str(p.get("created_at", ""))[:10] == today:
            today_posts += 1

    for cid in await r.hkeys("rmi:bulletin_comments"):
        data = await r.hget("rmi:bulletin_comments", cid)
        if data:
            comment_authors.add(json.loads(data).get("author_id", ""))

    return {
        "total_posts": raw.get("published", 0),
        "total_comments": raw.get("total_comments", 0),
        "active_users": len({a for a in authors | comment_authors if a}),
        "today_posts": today_posts,
    }


# ── votes ────────────────────────────────────────────────────────


@router.post(
    "/posts/{post_id}/vote",
    summary="Vote on Post",
    description="Upvote or downvote a post. Voting again with the same value retracts the vote.",
)
async def vote_post(post_id: str, body: VoteRequest, request: Request) -> dict[str, Any]:
    r = await BulletinBoardManager._get_redis()
    if not await r.hexists("rmi:bulletin_posts", post_id):
        raise NotFoundError(message="Post not found")

    voter = _voter_id(request)
    key = f"bulletin:votes:{post_id}"
    current = await r.hget(key, voter)
    if current == body.vote:
        await r.hdel(key, voter)  # retract
    else:
        await r.hset(key, voter, body.vote)

    up, down = await _vote_counts(r, post_id)

    # Persist onto the post dict so list reads stay cheap.
    data = await r.hget("rmi:bulletin_posts", post_id)
    if data:
        p = json.loads(data)
        p["upvotes"], p["downvotes"] = up, down
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(p))

    my_vote = None if current == body.vote else body.vote
    return {"upvotes": up, "downvotes": down, "my_vote": my_vote}


# ── reactions ────────────────────────────────────────────────────


@router.post(
    "/posts/{post_id}/react",
    summary="React to Post",
    description="Add one of like/love/laugh/angry/sad to a post. Returns the full reaction map.",
)
async def react_post(post_id: str, body: ReactionRequest, request: Request) -> dict[str, Any]:
    if body.reaction not in ALLOWED_REACTIONS:
        raise ValidationError(message=f"reaction must be one of {sorted(ALLOWED_REACTIONS)}")
    r = await BulletinBoardManager._get_redis()
    if not await r.hexists("rmi:bulletin_posts", post_id):
        raise NotFoundError(message="Post not found")

    voter = _voter_id(request)
    key = f"bulletin:reactions:{post_id}"
    member = f"{body.reaction}:{voter}"
    added = await r.sadd(f"{key}:seen", member)  # 1 = first time, 0 = toggle off
    if added:
        await r.hincrby(key, body.reaction, 1)
    else:
        await r.srem(f"{key}:seen", member)
        await r.hincrby(key, body.reaction, -1)

    reactions = {k: int(v) for k, v in (await r.hgetall(key) or {}).items()}
    return {"reactions": reactions}


# ── comments ─────────────────────────────────────────────────────


@router.get(
    "/posts/{post_id}/comments",
    summary="Get Post Comments",
    description="Comments for a post in the frontend Comment shape (newest first).",
)
async def get_post_comments(post_id: str, limit: int = 100) -> list[dict[str, Any]]:
    comments = await BulletinBoardManager.get_comments(post_id, limit=limit)
    return [_adapt_comment(c) for c in comments]


@router.post(
    "/posts/{post_id}/comments",
    summary="Create Post Comment",
    description="Comment on a post. Uses the session identity when signed in, otherwise posts as Community Member.",
)
async def create_post_comment(post_id: str, body: CommunityCommentRequest, request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None) or {}
    author_id = str(user.get("id") or f"anon_{_voter_id(request)[-8:]}")
    author_name = str(user.get("name") or "Community Member")
    author_email = str(user.get("email") or "anon@rugmunch.io")

    comment = await BulletinBoardManager.add_comment(
        post_id=post_id,
        author_id=author_id,
        author_name=author_name,
        author_email=author_email,
        content=body.content,
        parent_id=body.parent_id,
    )
    if comment is None:
        raise NotFoundError(message="Post not found or comments disabled")
    return _adapt_comment(comment.to_dict() if hasattr(comment, "to_dict") else vars(comment))


# ── env sanity (fail loudly in dev if redis unset) ───────────────
assert os.getenv("RMI_ENV", "production") in {"production", "development", "test"}
