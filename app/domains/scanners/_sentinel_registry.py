"""SENTINEL — Module Decorator & Central Registry.

THIN ADAPTER. Canonical source: ``rugmunch_scanner.detectors._sentinel_registry``.
The registry was superseded by the private rugmunch-scanner repo. This module
re-exports the scanner registry so the ``@sentinel_module`` decorators that now
live in ``rugmunch_scanner/detectors/*`` (fired by e.g.
``detectors/__init__.py`` when any scanner detector is imported) and the legacy
backend ``app/domains/scanners/*`` decorators all register into the SAME
``_SENTINEL_REGISTRY`` dict that ``sentinel_pipeline`` reads.

Without this unification, converting a sentinel-registered detector to a
scanner adapter would (a) register the module in the scanner registry only, and
(b) leave the backend registry missing ``contract_authority`` /
``proxy_detection`` / ``oracle_manipulation`` — breaking the FREE/PREMIUM split
and sentinel module discovery.
"""

from __future__ import annotations

from rugmunch_scanner.detectors._sentinel_registry import (
    _SENTINEL_REGISTRY,
    get_free_module_keys,
    get_premium_descriptions,
    get_premium_module_keys,
    sentinel_module,
)

__all__ = [
    "_SENTINEL_REGISTRY",
    "get_free_module_keys",
    "get_premium_descriptions",
    "get_premium_module_keys",
    "sentinel_module",
]
