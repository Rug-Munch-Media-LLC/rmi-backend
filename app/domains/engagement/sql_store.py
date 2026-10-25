"""SQLAlchemy implementation of :class:`EngagementStore` (Postgres canonical).

Only this module knows about sessions and the ORM; the router knows neither.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.domains.engagement.models import (
    ArticleComment,
    ArticleReaction,
    ArticleSentimentRollup,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

# Article ids are resolved against the content tables that own them; the pair is
# a module constant so no identifier is ever interpolated from user input.
ARTICLE_SOURCES: tuple[tuple[str, str], ...] = (
    ("news_items", "news_id"),
    ("crypto_news", "id"),
    ("content_posts", "id"),
)

_NOT_REMOVED = ArticleComment.status != "removed"


class SqlEngagementStore:
    """Request-scoped store; one instance per dependency resolution."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def article_exists(self, article_id: str) -> bool:
        for table, column in ARTICLE_SOURCES:
            stmt = text(f"SELECT 1 FROM {table} WHERE {column} = :article_id LIMIT 1")  # noqa: S608 — identifiers are module constants
            if (await self._session.execute(stmt, {"article_id": article_id})).first():
                return True
        return False

    async def add_reaction(self, article_id: str, user_id: str, kind: str) -> bool:
        self._session.add(ArticleReaction(article_id=article_id, user_id=user_id, kind=kind))
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            return False
        return True

    async def remove_reaction(self, article_id: str, user_id: str, kind: str) -> bool:
        result = await self._session.execute(
            delete(ArticleReaction).where(
                ArticleReaction.article_id == article_id,
                ArticleReaction.user_id == user_id,
                ArticleReaction.kind == kind,
            )
        )
        return bool(result.rowcount)

    async def reaction_counts(self, article_id: str) -> dict[str, int]:
        rows = await self._session.execute(
            select(ArticleReaction.kind, func.count())
            .where(ArticleReaction.article_id == article_id)
            .group_by(ArticleReaction.kind)
        )
        return {kind: int(count) for kind, count in rows.all()}

    async def user_reactions(self, article_id: str, user_id: str) -> list[str]:
        rows = await self._session.execute(
            select(ArticleReaction.kind).where(
                ArticleReaction.article_id == article_id, ArticleReaction.user_id == user_id
            )
        )
        return [kind for (kind,) in rows.all()]

    async def reactors(
        self, article_id: str, kind: str, *, limit: int, offset: int
    ) -> tuple[list[tuple[str, datetime]], int]:
        where = (ArticleReaction.article_id == article_id, ArticleReaction.kind == kind)
        total = await self._session.scalar(select(func.count()).select_from(ArticleReaction).where(*where))
        rows = await self._session.execute(
            select(ArticleReaction.user_id, ArticleReaction.created_at)
            .where(*where)
            .order_by(ArticleReaction.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), int(total or 0)

    async def create_comment(
        self, *, article_id: str, user_id: str, body: str, parent_id: str | None
    ) -> ArticleComment:
        comment = ArticleComment(article_id=article_id, user_id=user_id, body=body, parent_id=parent_id)
        self._session.add(comment)
        await self._session.flush()
        return comment

    async def get_comment(self, article_id: str, comment_id: str) -> ArticleComment | None:
        return await self._session.scalar(
            select(ArticleComment).where(ArticleComment.id == comment_id, ArticleComment.article_id == article_id)
        )

    async def update_comment(self, comment: ArticleComment, fields: dict[str, object]) -> ArticleComment:
        for name, value in fields.items():
            setattr(comment, name, value)
        await self._session.flush()
        return comment

    async def list_roots(
        self, article_id: str, *, limit: int, offset: int, newest_first: bool
    ) -> tuple[Sequence[ArticleComment], int]:
        where = (
            ArticleComment.article_id == article_id,
            ArticleComment.parent_id.is_(None),
            _NOT_REMOVED,
        )
        total = await self._session.scalar(select(func.count()).select_from(ArticleComment).where(*where))
        order = ArticleComment.created_at.desc() if newest_first else ArticleComment.created_at
        rows = await self._session.scalars(
            select(ArticleComment).where(*where).order_by(order).limit(limit).offset(offset)
        )
        return list(rows.all()), int(total or 0)

    async def list_replies(self, root_ids: Sequence[str]) -> Sequence[ArticleComment]:
        if not root_ids:
            return []
        rows = await self._session.scalars(
            select(ArticleComment)
            .where(ArticleComment.parent_id.in_(list(root_ids)), _NOT_REMOVED)
            .order_by(ArticleComment.created_at)
        )
        return list(rows.all())

    async def get_rollup(self, article_id: str) -> ArticleSentimentRollup | None:
        return await self._session.get(ArticleSentimentRollup, article_id)

    async def save_rollup(self, rollup: ArticleSentimentRollup) -> None:
        await self._session.merge(rollup)

    async def flush(self) -> None:
        await self._session.flush()
