"""Shared state for rag_service package."""

from __future__ import annotations
from app.crypto_embeddings import EmbeddingResult
from app.core.logging import get_logger

logger = get_logger(__name__)

_seeded = False
_pattern_cache: dict[str, EmbeddingResult] = {}
_pattern_cache_loaded = False
_redis_pool = None
