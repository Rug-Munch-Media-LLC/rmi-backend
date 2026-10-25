"""Reaction + sentiment routes for article engagement."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rate_limiter import RateLimitDependency
from app.domains.engagement.deps import get_optional_actor, get_store, require_actor
from app.domains.engagement.kinds import coerce_kind
from app.domains.engagement.permissions import Actor  # noqa: TC001 — FastAPI resolves annotations
from app.domains.engagement.port import EngagementStore  # noqa: TC001 — FastAPI resolves annotations
from app.domains.engagement.schemas import (
    ReactionRequest,
    ReactionSummary,
    ReactorOut,
    ReactorsResponse,
    SentimentResponse,
)
from app.domains.engagement.sentiment import derive
from app.domains.engagement.service import refresh_rollup, sentiment_response

router = APIRouter(tags=["engagement"])


def _summary(article_id: str, counts: dict[str, int], mine: list[str]) -> ReactionSummary:
    return ReactionSummary(article_id=article_id, counts=counts, total=sum(counts.values()), my_reactions=mine)


async def _require_article(store: EngagementStore, article_id: str) -> None:
    if not await store.article_exists(article_id):
        raise HTTPException(status_code=404, detail="article not found")


def _valid_kind(kind: str) -> str:
    if coerce_kind(kind) is None:
        raise HTTPException(status_code=422, detail=f"unknown reaction kind {kind!r}")
    return kind


async def _state(store: EngagementStore, article_id: str, actor: Actor | None) -> tuple[dict[str, int], list[str]]:
    counts = await store.reaction_counts(article_id)
    mine = await store.user_reactions(article_id, actor.user_id) if actor else []
    return counts, mine


@router.post(
    "/{article_id}/reactions",
    response_model=ReactionSummary,
    dependencies=[Depends(RateLimitDependency(limit_key="article_react", rate=2.0, burst=30))],
)
async def react(
    article_id: str,
    payload: ReactionRequest,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
) -> ReactionSummary:
    """Add a reaction. Repeating the same kind is a no-op, never a duplicate row."""
    await _require_article(store, article_id)
    await store.add_reaction(article_id, actor.user_id, _valid_kind(payload.kind))
    await refresh_rollup(store, article_id)
    counts, mine = await _state(store, article_id, actor)
    return _summary(article_id, counts, mine)


@router.delete("/{article_id}/reactions/{kind}", response_model=ReactionSummary)
async def unreact(
    article_id: str,
    kind: str,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
) -> ReactionSummary:
    """Remove a reaction. Removing one that is not there is a no-op."""
    await _require_article(store, article_id)
    await store.remove_reaction(article_id, actor.user_id, _valid_kind(kind))
    await refresh_rollup(store, article_id)
    counts, mine = await _state(store, article_id, actor)
    return _summary(article_id, counts, mine)


@router.get("/{article_id}/reactions", response_model=ReactionSummary)
async def list_reactions(
    article_id: str,
    actor: Actor | None = Depends(get_optional_actor),
    store: EngagementStore = Depends(get_store),
) -> ReactionSummary:
    """Public read: counts for everyone, personal state only for the caller."""
    await _require_article(store, article_id)
    counts, mine = await _state(store, article_id, actor)
    return _summary(article_id, counts, mine)


@router.get("/{article_id}/reactions/{kind}/users", response_model=ReactorsResponse)
async def list_reactors(
    article_id: str,
    kind: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: EngagementStore = Depends(get_store),
) -> ReactorsResponse:
    """Who reacted, oldest first. User ids only — no profile data is joined."""
    await _require_article(store, article_id)
    rows, total = await store.reactors(article_id, _valid_kind(kind), limit=limit, offset=offset)
    return ReactorsResponse(
        kind=kind,
        items=[ReactorOut(user_id=user_id, created_at=created_at) for user_id, created_at in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{article_id}/sentiment", response_model=SentimentResponse)
async def get_sentiment(
    article_id: str,
    store: EngagementStore = Depends(get_store),
) -> SentimentResponse:
    """Published rollup. ``score`` stays null until the evidence clears the bar."""
    await _require_article(store, article_id)
    rollup = await store.get_rollup(article_id)
    if rollup is None:
        return sentiment_response(article_id, derive({}))
    return sentiment_response(article_id, rollup, rollup.updated_at)
