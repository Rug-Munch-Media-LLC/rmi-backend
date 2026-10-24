"""Engagement orchestration: rollup refresh and thread assembly.

Keeps the router free of loop/mapping logic so each route stays a thin, readable
translation between HTTP and the store contract.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.domains.engagement.models import ArticleComment, ArticleSentimentRollup
from app.domains.engagement.permissions import Actor, CommentState
from app.domains.engagement.schemas import CommentOut, SentimentResponse
from app.domains.engagement.sentiment import SentimentRollup, derive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.domains.engagement.port import EngagementStore

_MASKED_STATUSES: frozenset[str] = frozenset({"hidden", "removed"})


def comment_state(comment: ArticleComment) -> CommentState:
    """Project a comment row onto the moderation-relevant fields."""
    return CommentState(user_id=comment.user_id, status=comment.status, deleted=comment.deleted_at is not None)


def to_comment_out(comment: ArticleComment, actor: Actor | None = None) -> CommentOut:
    """API projection. Deleted rows keep their id and timestamps only."""
    deleted = comment.deleted_at is not None
    masked = deleted or comment.status in _MASKED_STATUSES
    return CommentOut(
        id=comment.id,
        article_id=comment.article_id,
        parent_id=comment.parent_id,
        user_id=comment.user_id,
        status=comment.status,
        body=None if masked else comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        deleted=deleted,
        report_count=comment.report_count,
        is_mine=bool(actor and actor.user_id == comment.user_id),
    )


def build_thread(
    roots: Sequence[ArticleComment],
    replies: Sequence[ArticleComment],
    actor: Actor | None = None,
) -> list[CommentOut]:
    """Attach replies to their root. Depth is one, so this is a single pass."""
    grouped: dict[str, list[CommentOut]] = defaultdict(list)
    for reply in replies:
        if reply.parent_id:
            grouped[reply.parent_id].append(to_comment_out(reply, actor))
    tree: list[CommentOut] = []
    for root in roots:
        node = to_comment_out(root, actor)
        node.replies = grouped.get(root.id, [])
        tree.append(node)
    return tree


async def refresh_rollup(
    store: EngagementStore, article_id: str, *, classifier_score: float | None = None
) -> SentimentRollup:
    """Recompute and persist the rollup from observed signal, then return it."""
    derived = derive(await store.reaction_counts(article_id), classifier_score=classifier_score)
    await store.save_rollup(
        ArticleSentimentRollup(
            article_id=article_id,
            positive=derived.positive,
            neutral=derived.neutral,
            negative=derived.negative,
            sample_size=derived.sample_size,
            score=derived.score,
            reason=derived.reason,
        )
    )
    return derived


def sentiment_response(article_id: str, derived: SentimentRollup, updated_at: object = None) -> SentimentResponse:
    """Map a derivation onto the wire shape (``score`` may be ``None``)."""
    return SentimentResponse(
        article_id=article_id,
        positive=derived.positive,
        neutral=derived.neutral,
        negative=derived.negative,
        sample_size=derived.sample_size,
        score=derived.score,
        reason=derived.reason,
        updated_at=updated_at,
    )
