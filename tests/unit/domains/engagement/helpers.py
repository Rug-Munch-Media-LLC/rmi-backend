"""In-memory ``EngagementStore`` — a real implementation of the same contract.

Not a mock: it enforces the same uniqueness, scoping and status rules as the SQL
store, so a test that passes here passes against the production code path.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import TYPE_CHECKING

from app.domains.engagement.models import ArticleComment, ArticleSentimentRollup

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

# Articles a content surface is expected to own. Anything else 404s, which is
# what makes the unknown-article paths testable.
SEEDED_ARTICLES: frozenset[str] = frozenset({"art-1", "art-2"})


def _comment(**fields: object) -> ArticleComment:
    defaults: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "status": "visible",
        "report_count": 0,
        "reported_by": [],
        "edited_at": None,
        "deleted_at": None,
    }
    defaults.update(fields)
    return ArticleComment(**defaults)


class InMemoryEngagementStore:
    """Dict-backed store. Insertion order is creation order, so reads are stable."""

    def __init__(self, articles: set[str] | None = None) -> None:
        self.articles = set(SEEDED_ARTICLES if articles is None else articles)
        self.reactions: dict[tuple[str, str, str], datetime] = {}
        self.comments: dict[str, ArticleComment] = {}
        self.rollups: dict[str, ArticleSentimentRollup] = {}

    async def article_exists(self, article_id: str) -> bool:
        return article_id in self.articles

    async def add_reaction(self, article_id: str, user_id: str, kind: str) -> bool:
        key = (article_id, user_id, kind)
        if key in self.reactions:
            return False
        from datetime import UTC, datetime

        self.reactions[key] = datetime.now(UTC)
        return True

    async def remove_reaction(self, article_id: str, user_id: str, kind: str) -> bool:
        return self.reactions.pop((article_id, user_id, kind), None) is not None

    async def reaction_counts(self, article_id: str) -> dict[str, int]:
        return dict(Counter(kind for (article, _user, kind) in self.reactions if article == article_id))

    async def user_reactions(self, article_id: str, user_id: str) -> list[str]:
        return [kind for (article, user, kind) in self.reactions if article == article_id and user == user_id]

    async def reactors(
        self, article_id: str, kind: str, *, limit: int, offset: int
    ) -> tuple[list[tuple[str, datetime]], int]:
        matching = [
            (user, created)
            for (article, user, reaction_kind), created in self.reactions.items()
            if article == article_id and reaction_kind == kind
        ]
        return matching[offset : offset + limit], len(matching)

    async def create_comment(
        self, *, article_id: str, user_id: str, body: str, parent_id: str | None
    ) -> ArticleComment:
        from datetime import UTC, datetime

        comment = _comment(
            article_id=article_id,
            user_id=user_id,
            body=body,
            parent_id=parent_id,
            created_at=datetime.now(UTC),
        )
        self.comments[comment.id] = comment
        return comment

    async def get_comment(self, article_id: str, comment_id: str) -> ArticleComment | None:
        comment = self.comments.get(comment_id)
        if comment is None or comment.article_id != article_id:
            return None
        return comment

    async def update_comment(self, comment: ArticleComment, fields: dict[str, object]) -> ArticleComment:
        for name, value in fields.items():
            setattr(comment, name, value)
        return comment

    async def list_roots(
        self, article_id: str, *, limit: int, offset: int, newest_first: bool
    ) -> tuple[Sequence[ArticleComment], int]:
        roots = [
            comment
            for comment in self.comments.values()
            if comment.article_id == article_id and comment.parent_id is None and comment.status != "removed"
        ]
        ordered = list(reversed(roots)) if newest_first else roots
        return ordered[offset : offset + limit], len(roots)

    async def list_replies(self, root_ids: Sequence[str]) -> Sequence[ArticleComment]:
        wanted = set(root_ids)
        return [
            comment for comment in self.comments.values() if comment.parent_id in wanted and comment.status != "removed"
        ]

    async def get_rollup(self, article_id: str) -> ArticleSentimentRollup | None:
        return self.rollups.get(article_id)

    async def save_rollup(self, rollup: ArticleSentimentRollup) -> None:
        from datetime import UTC, datetime

        rollup.updated_at = datetime.now(UTC)
        self.rollups[rollup.article_id] = rollup

    async def flush(self) -> None:
        return None
