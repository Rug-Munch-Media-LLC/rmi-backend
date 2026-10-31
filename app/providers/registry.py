"""Process-wide provider singleton and factory."""

from __future__ import annotations

from app.providers.unified import UnifiedProvider

_provider: UnifiedProvider | None = None


def get_unified_provider() -> UnifiedProvider:
    """Return the one shared provider instance (created on first call)."""
    global _provider
    if _provider is None:
        _provider = UnifiedProvider()
    return _provider
