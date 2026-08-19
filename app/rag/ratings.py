"""Article rating stub — rating pipeline lives in rugbrain."""

from typing import Any


async def rate_article(article: dict[str, Any]) -> dict[str, Any]:
    """Stub: article rating has been migrated to rugbrain."""
    return {"score": 0.5, "rated": False, "source": "rugbrain"}
