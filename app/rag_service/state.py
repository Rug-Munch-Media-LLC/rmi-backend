"""Shared state for rag_service package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.crypto_embeddings import EmbeddingResult

logger = get_logger(__name__)

_seeded = False
_pattern_cache: dict[str, EmbeddingResult] = {}
_pattern_cache_loaded = False
_redis_pool: Any = None
