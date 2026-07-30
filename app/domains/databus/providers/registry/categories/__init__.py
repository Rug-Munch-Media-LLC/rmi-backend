"""
DataBus Provider Categories
============================
Category-specific provider chain builders for the DataBus provider registry.
Each module builds the provider chain for a specific data domain.

Usage:
    from app.domains.databus.providers.registry.categories import (
        build_intel_chains,
        build_market_chains,
        build_security_chains,
        build_social_chains,
        build_system_chains,
        build_wallet_chains,
    )
"""

from __future__ import annotations

from app.domains.databus.providers.registry.categories.intel import build_intel_chains
from app.domains.databus.providers.registry.categories.market import build_market_chains
from app.domains.databus.providers.registry.categories.security import build_security_chains
from app.domains.databus.providers.registry.categories.social import build_social_chains
from app.domains.databus.providers.registry.categories.system import build_system_chains
from app.domains.databus.providers.registry.categories.wallet import build_wallet_chains

__all__ = [
    "build_intel_chains",
    "build_market_chains",
    "build_security_chains",
    "build_social_chains",
    "build_system_chains",
    "build_wallet_chains",
]
