"""Multi-level news deduplication for the RugBrain RAG pipeline.

Levels (checked in order, all fail-open — a backend outage never blocks
ingestion):

  Level 1 — URL dedup:        exact URL hash in Redis ZSET ``rag:dedup:urls``
                              (member = sha256(url), score = unix ts, 30-day
                              per-member TTL enforced by lazy pruning).
  Level 2 — Title similarity: embed the title, search the Qdrant collection,
                              cosine similarity >= 0.95 → duplicate.
  Level 3 — Content hash:     ``content_fingerprint()`` (boilerplate stripped,
                              first 1000 chars, whitespace normalized) stored
                              in Redis ZSET ``rag:dedup:content:{collection}``.
  Level 4 — Entity overlap:   crypto entities (tokens, protocols, people,
                              addresses) overlap coefficient > 0.8 AND both
                              articles published within 6 hours → same story.

CryptoPanic-style grouping: the first article of a story receives a stable
``group_id`` (``grp:{sha256(url)[:12]}``). Duplicate variations are blocked
from ingestion but recorded as members of the existing group, so a story is
represented once in the corpus while every source URL maps back to its group.

Fail-open contract: every public check catches backend exceptions (Redis
auth/down, Qdrant unreachable, embedder unavailable) and returns
"not a duplicate" so ingestion always proceeds when dedup can't decide.

This module is a FACADE. The implementation is split into single-purpose
modules (``dedup_config``, ``dedup_urls``, ``dedup_addresses``,
``dedup_content``, ``dedup_hashing``, ``dedup_entities``, ``dedup_groups``,
``dedup_titles``, ``dedup_pipeline``, ``dedup_vocabulary``) and every public
name that used to live here is re-exported unchanged.

Injection seams (no patching):
  * Redis  — ``bind_redis(client)`` sets a ContextVar for the current task;
             ``unbind_redis(token)`` / ``reset_redis_client()`` undo it.
  * Search — ``bind_title_search(fn)`` / ``reset_title_search(token)`` set a
             ContextVar read by ``check_duplicates``;
             ``is_duplicate_by_title(..., search=<async fn>)`` also takes one.
"""

from __future__ import annotations

from app.utils.dedup_addresses import is_valid_evm_address, normalize_address, to_checksum_address
from app.utils.dedup_config import (
    CONTENT_DEDUP_TTL_DAYS,
    ENTITY_OVERLAP_THRESHOLD,
    ENTITY_WINDOW_HOURS,
    GROUP_MEMBERS_TTL_DAYS,
    TITLE_SIM_THRESHOLD,
    URL_DEDUP_TTL_DAYS,
    bind_redis,
    reset_redis_client,
    unbind_redis,
)
from app.utils.dedup_content import (
    content_fingerprint,
    is_duplicate_content,
    lookup_group_by_fp,
    mark_content_ingested,
    strip_boilerplate,
)
from app.utils.dedup_entities import (
    extract_crypto_entities,
    is_duplicate_by_entities,
    parse_published_at,
    within_window,
)
from app.utils.dedup_groups import new_group_id, record_fp_group, record_group_membership
from app.utils.dedup_hashing import content_hash, doc_id_hash, near_duplicate, normalize_text, tokenize, url_hash
from app.utils.dedup_pipeline import (
    DedupResult,
    active_title_search,
    bind_title_search,
    check_duplicates,
    reset_title_search,
)
from app.utils.dedup_titles import TitleSearch, is_duplicate_by_title, search_titles
from app.utils.dedup_urls import is_duplicate_url, lookup_group_by_url, mark_url_ingested
from app.utils.dedup_vocabulary import KNOWN_PEOPLE, KNOWN_PROTOCOLS, KNOWN_TOKENS

# Back-compat alias for the old test helper.
_reset_redis_client_for_tests = reset_redis_client

__all__ = [
    "CONTENT_DEDUP_TTL_DAYS",
    "ENTITY_OVERLAP_THRESHOLD",
    "ENTITY_WINDOW_HOURS",
    "GROUP_MEMBERS_TTL_DAYS",
    "KNOWN_PEOPLE",
    "KNOWN_PROTOCOLS",
    "KNOWN_TOKENS",
    "TITLE_SIM_THRESHOLD",
    "URL_DEDUP_TTL_DAYS",
    "DedupResult",
    "TitleSearch",
    "active_title_search",
    "bind_redis",
    "bind_title_search",
    "check_duplicates",
    "content_fingerprint",
    "content_hash",
    "doc_id_hash",
    "extract_crypto_entities",
    "is_duplicate_by_entities",
    "is_duplicate_by_title",
    "is_duplicate_content",
    "is_duplicate_url",
    "is_valid_evm_address",
    "lookup_group_by_fp",
    "lookup_group_by_url",
    "mark_content_ingested",
    "mark_url_ingested",
    "near_duplicate",
    "new_group_id",
    "normalize_address",
    "normalize_text",
    "parse_published_at",
    "record_fp_group",
    "record_group_membership",
    "reset_redis_client",
    "reset_title_search",
    "search_titles",
    "strip_boilerplate",
    "to_checksum_address",
    "tokenize",
    "unbind_redis",
    "url_hash",
    "within_window",
]
