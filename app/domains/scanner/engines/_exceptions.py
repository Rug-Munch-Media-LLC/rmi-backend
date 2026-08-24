"""Scanner-local exceptions — zero backend dependency.

Extraction-prep (repo split): ``RAGUnavailableError`` lived in
``app.rag.embeddings``; the scanner now owns its copy. After the split,
the backend imports FROM the scanner package (single source of truth).
"""

from __future__ import annotations


class RAGUnavailableError(Exception):
    """RAG system is unavailable."""

# ── Redis access port (phase 1b) ──────────────────────────────────────
# The scanner needs ONE thing from the backend redis pool: an async
# client. Binding it through this module keeps engines free of
# app.core.redis; post-split, set_redis_port() wires a standalone pool.

from typing import Any, Callable

_redis_factory: Callable[[], Any] | None = None


def set_redis_port(factory: Callable[[], Any]) -> None:
    """Bind the redis client factory (backend wiring / tests)."""
    global _redis_factory
    _redis_factory = factory


def get_redis_async() -> Any:
    """Return the async redis client via the bound factory."""
    if _redis_factory is None:
        # Lazy default binding to the backend pool (pre-split behavior).
        from app.domains.scanner.engines._exceptions import get_redis_async as _backend_get

        set_redis_port(_backend_get)
    return _redis_factory()
