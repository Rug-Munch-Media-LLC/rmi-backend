"""
Unified API Key Registry - Package
====================================
Multi-Key Pools with Load Balancing. Split into:
  - config:   ProviderConfig dataclass + PROVIDER_REGISTRY definitions
  - keys:     ApiKey dataclass, discover_keys, KeyPool load balancer
  - manager:  UnifiedApiManager singleton, get_api_manager, capacity_report
  - sources:  BACKEND_SOURCES registry, list_all_sources, _summarize_all
"""

from __future__ import annotations

from .config import PROVIDER_REGISTRY, ProviderConfig
from .keys import ApiKey, KeyPool, discover_keys
from .manager import UnifiedApiManager, get_api_manager
from .sources import BACKEND_SOURCES, list_all_sources

__all__ = [
    "BACKEND_SOURCES",
    "PROVIDER_REGISTRY",
    "ApiKey",
    "KeyPool",
    "ProviderConfig",
    "UnifiedApiManager",
    "discover_keys",
    "get_api_manager",
    "list_all_sources",
]
