"""Multi-level news deduplication for the RugBrain RAG pipeline.

Levels (checked in order, all fail-open — a backend outage never blocks
ingestion):

  Level 1 — URL dedup:        exact URL hash in Redis ZSET ``rag:dedup:urls``
  Level 2 — Title similarity: embed the title, search the Qdrant collection,
                              cosine similarity >= 0.95 → duplicate.
  Level 3 — Content hash:     ``content_fingerprint()`` stored in Redis ZSET.
  Level 4 — Entity overlap:   crypto entities overlap coefficient > 0.8 AND
                              both articles published within 6 hours → same story.

Implementations live in sub-modules. This package re-exports the public
surface so existing imports keep working.
"""

from __future__ import annotations

from app.rag.dedup._base import (
    DedupResult,
    content_fingerprint,
    content_hash,
    is_valid_evm_address,
    near_duplicate,
    normalize_address,
    normalize_text,
    to_checksum_address,
    tokenize,
)
from app.rag.dedup.content import (
    is_duplicate_content,
    lookup_group_by_fp,
    mark_content_ingested,
)
from app.rag.dedup.entity import (
    extract_crypto_entities,
    is_duplicate_by_entities,
    parse_ts,
    within_window,
)
from app.rag.dedup.grouping import (
    group_from_hit,
    new_group_id,
    record_fp_group,
    record_group_membership,
)
from app.rag.dedup.url import (
    is_duplicate_url,
    lookup_group_by_url,
    mark_url_ingested,
    prune_zset,
    url_hash,
)

__all__ = [
    "DedupResult",
    "content_fingerprint",
    "content_hash",
    "doc_id_hash",
    "extract_crypto_entities",
    "group_from_hit",
    "is_duplicate_by_entities",
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
    "parse_ts",
    "prune_zset",
    "record_fp_group",
    "record_group_membership",
    "to_checksum_address",
    "tokenize",
    "url_hash",
    "within_window",
]
