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

from collections.abc import Callable
from typing import Any

_redis_factory: Callable[[], Any] | None = None


def set_redis_port(factory: Callable[[], Any]) -> None:
    """Bind the redis client factory (backend wiring / tests)."""
    global _redis_factory
    _redis_factory = factory


def get_redis_async() -> Any:
    """Return the async redis client via the bound factory.

    2026-08-26 FIX: the lazy default imported THIS function from this same
    module and bound it as its own factory → infinite recursion on first
    call (RecursionError swallowed upstream → every funding/freshness
    engine silently returned nulls). Bind the real backend pool instead.
    """
    if _redis_factory is None:
        try:
            # ASYNC pool — engines await this client (scoring.py, scanner.py,
            # persistence.py all `await r.exists(...)` etc.).
            from app.core.redis import get_redis_async as _backend_async_get

            set_redis_port(_backend_async_get)
        except Exception:
            return None
    return _redis_factory()
