"""Orchestrator: run the four dedup levels in order; stop at the first hit.

Order: URL (cheap) → content fingerprint (cheap) → title (embed) → entities
(against the same search hits). Every level fails open, so a backend outage
never blocks ingestion — the article is simply treated as new.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.utils.dedup_config import TITLE_SIM_THRESHOLD
from app.utils.dedup_content import content_fingerprint, is_duplicate_content, lookup_group_by_fp
from app.utils.dedup_entities import is_duplicate_by_entities, parse_published_at, within_window
from app.utils.dedup_groups import new_group_id
from app.utils.dedup_titles import TitleSearch, search_titles
from app.utils.dedup_urls import is_duplicate_url, lookup_group_by_url

log = get_logger(__name__)

# ── Title-search DI seam (same pattern as ``dedup_config.bind_redis``) ───

_TITLE_SEARCH: ContextVar[TitleSearch | None] = ContextVar("dedup_title_search", default=None)


def bind_title_search(fn: TitleSearch) -> Token:
    """Bind *fn* as the title search for the current context; returns the token."""
    return _TITLE_SEARCH.set(fn)


def reset_title_search(token: Token) -> None:
    """Undo a :func:`bind_title_search` call."""
    _TITLE_SEARCH.reset(token)


def active_title_search() -> TitleSearch:
    """Resolve the title-search callable: bound override, else the Qdrant search."""
    return _TITLE_SEARCH.get() or search_titles


@dataclass
class DedupResult:
    """Outcome of the multi-level dedup check.

    ``level`` is the level that fired (``url`` | ``title`` | ``content`` |
    ``entity``). ``group_id`` is the existing story group on a duplicate, else
    a fresh one. ``details`` carries level diagnostics (fp, score).
    """

    is_duplicate: bool = False
    level: str | None = None
    group_id: str | None = None
    matched_doc_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def group_from_hit(hit: Any) -> str | None:
    """Read a group_id from a search hit's metadata, if present."""
    try:
        g = hit.metadata.get("group_id")
        return str(g) if g else None
    except Exception:
        log.debug("group_from_hit_failed", exc_info=True)
        return None


def _group_for_hit(hit: Any) -> str | None:
    """Hit metadata group_id, else the group mapped to the hit's source URL."""
    return group_from_hit(hit) or lookup_group_by_url(str(hit.metadata.get("source_url", "")))


async def check_duplicates(
    url: str,
    title: str,
    content: str,
    collection: str = "news_v2",
    published_at: str = "",
) -> DedupResult:
    """Run all four dedup levels in order; stop at the first duplicate.

    Order: URL (cheap) → content fingerprint (cheap) → title (embed) →
    entities (uses the same search hits for candidate articles).

    Every level fails open: backend errors are logged at DEBUG and treated
    as "not a duplicate", so ingestion is never blocked by dedup outages.

    Args:
        url: Canonical article URL.
        title: Article title.
        content: Article body (markdown/text).
        collection: RAG collection (default ``news_v2``).
        published_at: ISO/RSS timestamp of the article; used for the Level 4
            six-hour window check. Empty → window check skipped.

    Returns:
        A ``DedupResult``. When ``is_duplicate`` is True the caller should
        NOT ingest; ``group_id`` carries the existing story group.
    """
    # Level 1: URL
    if is_duplicate_url(url):
        group = lookup_group_by_url(url)
        log.debug("dedup_level=url url=%s group=%s", url, group)
        return DedupResult(is_duplicate=True, level="url", group_id=group)

    # Level 3: content fingerprint (cheap, no network)
    fp = content_fingerprint(content)
    if is_duplicate_content(fp, collection):
        group = lookup_group_by_fp(fp, collection)
        log.debug("dedup_level=content fp=%s group=%s", fp[:12], group)
        return DedupResult(is_duplicate=True, level="content", group_id=group, details={"fp": fp})

    # Level 2 + Level 4 share a single search.
    hits: list[Any] = []
    try:
        hits = await active_title_search()(title, collection, 5)
    except Exception as e:
        log.debug("dedup_title_search_failed err=%s allow=True", e)
        hits = []

    # Level 2: title similarity
    for h in hits:
        if float(h.score) >= TITLE_SIM_THRESHOLD:
            group = _group_for_hit(h)
            log.debug("dedup_level=title url=%s score=%s group=%s", url, round(float(h.score), 4), group)
            return DedupResult(
                is_duplicate=True,
                level="title",
                group_id=group,
                matched_doc_id=h.doc_id,
                details={"score": float(h.score)},
            )

    # Level 4: entity overlap against candidates within the time window
    new_ts = parse_published_at(published_at)
    for h in hits:
        cand_ts = parse_published_at(str(h.metadata.get("published_at") or h.metadata.get("date") or ""))
        if not within_window(new_ts, cand_ts):
            continue
        if is_duplicate_by_entities(content, str(h.text or "")):
            group = _group_for_hit(h)
            log.debug("dedup_level=entity url=%s matched=%s group=%s", url, h.doc_id, group)
            return DedupResult(is_duplicate=True, level="entity", group_id=group, matched_doc_id=h.doc_id)

    # No duplicate found → fresh story (or a variation we can't prove).
    return DedupResult(is_duplicate=False, group_id=new_group_id(url), details={"fp": fp})
