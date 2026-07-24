"""
Entity Extraction + Exact-Match Lookup for Crypto RAG
======================================================
Fast regex-based entity extraction for crypto-specific entities.
Redis-backed exact-match index that bypasses vector similarity for
entity-heavy queries.

Usage:
    from app.entity_extraction import extract_entities, EntityExtractionResult
    from app.entity_extraction import EntityLookup, get_entity_lookup
    from app.entity_extraction import hybrid_query, reciprocal_rank_fusion
"""

from __future__ import annotations

from app.entity_extraction.extractor import extract_entities
from app.entity_extraction.lookup import EntityLookup
from app.entity_extraction.models import EntityExtractionResult
from app.entity_extraction.query import (
    get_entity_lookup,
    hybrid_query,
    reciprocal_rank_fusion,
)

__all__ = [
    "EntityExtractionResult",
    "EntityLookup",
    "extract_entities",
    "get_entity_lookup",
    "hybrid_query",
    "reciprocal_rank_fusion",
]
