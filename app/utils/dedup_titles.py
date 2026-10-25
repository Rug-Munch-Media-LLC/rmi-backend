"""Level 2: title-similarity dedup against the vector collection.

The search backend is injected (``search=``) instead of patched: the default
uses ``rugbrain_search`` against the Qdrant collection, and any async
``(title, collection, limit) -> list[Hit]`` callable can replace it. Fail-open.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.utils.dedup_config import TITLE_SIM_THRESHOLD

log = get_logger(__name__)

# (title, collection, limit) -> hits with ``.score`` / ``.doc_id`` / ``.text`` / ``.metadata``
TitleSearch = Callable[[str, str, int], Awaitable[list[Any]]]


async def search_titles(title: str, collection: str, limit: int = 5) -> list[Any]:
    """Default title search: vector-search the Qdrant collection.

    Reuses the engine's fail-open ``rugbrain_search`` (handles legacy + named
    vectors, returns [] when Qdrant is unreachable). Returns engine ``Hit``
    objects with ``{doc_id, score, text, metadata}``.
    """
    if not title or not title.strip():
        return []
    from app.integrations.rugbrain_client import rugbrain_search

    return await rugbrain_search(query="", collections=[collection], limit=limit)


async def is_duplicate_by_title(title: str, collection: str = "news_v2", search: TitleSearch | None = None) -> bool:
    """True when an existing article has a nearly identical title.

    Any hit with cosine similarity >= ``TITLE_SIM_THRESHOLD`` (0.95) counts as
    a duplicate. Fail-open: embedder/Qdrant unavailable → False.
    """
    try:
        hits = await (search or search_titles)(title, collection, 5)
    except Exception as e:
        log.debug("dedup_title_check_failed title=%r err=%s allow=True", title, e)
        return False
    return any(float(h.score) >= TITLE_SIM_THRESHOLD for h in hits)
