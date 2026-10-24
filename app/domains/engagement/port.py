"""Storage contract for article engagement.

The router depends on this protocol, never on SQLAlchemy, so the API is tested
against a real in-memory implementation of the same contract instead of a mock.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003 — needs the runtime module object
from datetime import datetime  # noqa: TC003 — needs the runtime module object
from typing import Protocol

from app.domains.engagement.models import (  # noqa: TC001 — runtime annotation parity
    ArticleComment,
    ArticleSentimentRollup,
)


class EngagementStore(Protocol):
    """Every write is idempotent and every read is scoped by ``article_id``."""

    async def article_exists(self, article_id: str) -> bool:
        """True when the article id resolves to a real content row."""
        ...

    async def add_reaction(self, article_id: str, user_id: str, kind: str) -> bool:
        """Add a reaction. Returns False when it already existed."""
        ...

    async def remove_reaction(self, article_id: str, user_id: str, kind: str) -> bool:
        """Remove a reaction. Returns False when there was nothing to remove."""
        ...

    async def reaction_counts(self, article_id: str) -> dict[str, int]:
        """Per-kind counts for the article."""
        ...

    async def user_reactions(self, article_id: str, user_id: str) -> list[str]:
        """Kinds this user has reacted with."""
        ...

    async def reactors(
        self, article_id: str, kind: str, *, limit: int, offset: int
    ) -> tuple[list[tuple[str, datetime]], int]:
        """(page of (user_id, created_at), total) for one kind."""
        ...

    async def create_comment(
        self, *, article_id: str, user_id: str, body: str, parent_id: str | None
    ) -> ArticleComment:
        """Insert a comment row."""
        ...

    async def get_comment(self, article_id: str, comment_id: str) -> ArticleComment | None:
        """Load a comment scoped to the article — prevents cross-article IDOR."""
        ...

    async def update_comment(self, comment: ArticleComment, fields: dict[str, object]) -> ArticleComment:
        """Apply a field patch and persist it."""
        ...

    async def list_roots(
        self, article_id: str, *, limit: int, offset: int, newest_first: bool
    ) -> tuple[Sequence[ArticleComment], int]:
        """(page of top-level comments, total) excluding removed rows."""
        ...

    async def list_replies(self, root_ids: Sequence[str]) -> Sequence[ArticleComment]:
        """All visible/hidden replies under the given roots, oldest first."""
        ...

    async def get_rollup(self, article_id: str) -> ArticleSentimentRollup | None:
        """Current rollup row, or None when never computed."""
        ...

    async def save_rollup(self, rollup: ArticleSentimentRollup) -> None:
        """Insert or update the rollup row for its article."""
        ...

    async def flush(self) -> None:
        """Flush pending changes without ending the request transaction."""
        ...
