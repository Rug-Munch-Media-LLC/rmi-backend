"""
SENTINEL — Module Decorator & Central Registry
==============================================
``@sentinel_module`` auto-registers scanner classes into a central
``_SENTINEL_REGISTRY`` so that adding a new detector no longer requires
touching 5 files (imports, ``_ensure_modules()``, ``_get_analyzers()``,
FREE/PREMIUM tuples, and descriptions dict).

Drop a .py file into ``app/domains/scanners/``, decorate the class, and
the pipeline picks it up automatically.

Backward Compat
---------------
The existing manual singleton variables (``_holder_analyzer``, etc.) in
``modules.py`` and the hardcoded tuples in ``sentinel_phase.py`` still
work.  The registry is additive — modules may be registered via decorator
OR the legacy manual path.
"""

from __future__ import annotations

from typing import Any

# ── Central Registry ──────────────────────────────────────────────────
# Populated at import time by ``@sentinel_module`` decorators on scanner
# classes.  Keyed by the module key string (e.g. ``"holder_analysis"``).
_SENTINEL_REGISTRY: dict[str, dict[str, Any]] = {}


def sentinel_module(
    key: str,
    tier: str = "premium",
    description: str = "",
    method: str = "analyze",
    needs_deployer: bool = False,
    chain_gated: str | None = None,
) -> Any:
    """Decorator that auto-registers a SENTINEL scanner module.

    Usage::

        from app.domains.scanners._sentinel_registry import sentinel_module

        @sentinel_module(key="holder_concentration", tier="free",
                          description="Holder concentration (HHI-based)")
        class HolderConcentrationAnalyzer:
            async def analyze(self, address: str, chain: str) -> dict:
                ...

    Parameters
    ----------
    key:
        Module key used throughout the pipeline (runners, risk extractors,
        report field names).  Must be unique.
    tier:
        ``"free"`` (always runs for all users) or ``"premium"`` (gated
        behind elite/admin tier).
    description:
        Human-readable one-liner shown in premium-preview responses.
    method:
        Name of the async method to call on the singleton instance.
        Defaults to ``"analyze"``.  Override for modules with non-standard
        method names (e.g. ``detect_bundles``, ``verify_lock``).
    needs_deployer:
        If ``True`` the module requires a deployer address to run.  When no
        deployer can be resolved it is skipped gracefully.
    chain_gated:
        Optional chain name (e.g. ``"solana"``) — the module only runs for
        that chain.  Skipped with a ``None`` result for other chains.
    """

    def decorator(cls: type) -> type:
        _SENTINEL_REGISTRY[key] = {
            "class": cls,
            "tier": tier,
            "description": description,
            "method": method,
            "needs_deployer": needs_deployer,
            "chain_gated": chain_gated,
        }
        return cls

    return decorator


def get_free_module_keys() -> tuple[str, ...]:
    """Return sorted module keys registered at the ``"free"`` tier."""
    return tuple(sorted(k for k, v in _SENTINEL_REGISTRY.items() if v.get("tier") == "free"))


def get_premium_module_keys() -> tuple[str, ...]:
    """Return sorted module keys registered at the ``"premium"`` tier."""
    return tuple(sorted(k for k, v in _SENTINEL_REGISTRY.items() if v.get("tier") == "premium"))


def get_premium_descriptions() -> dict[str, str]:
    """Build ``{key: description}`` for every premium-tier module."""
    return {
        k: v["description"]
        for k, v in _SENTINEL_REGISTRY.items()
        if v.get("tier") == "premium" and v.get("description")
    }
