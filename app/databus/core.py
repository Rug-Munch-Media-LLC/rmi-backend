"""
DataBus Core — Re-export facade for service modules.

Every data request in the entire platform routes through DataBus.
This module re-exports the public API from the services/ package
so existing consumers (``from app.databus.core import DataBus``)
continue to work without modification.

Usage:
    from app.databus.core import DataBus, RequestDeduplicator, databus
"""

from __future__ import annotations

from app.databus.services.client import DataBus
from app.databus.services.dedup import RequestDeduplicator
from app.databus.services.health import ProviderHealthTracker

# ── Singleton ───────────────────────────────────────────────────────────────────

databus: DataBus = DataBus()

__all__ = [
    "DataBus",
    "ProviderHealthTracker",
    "RequestDeduplicator",
    "databus",
]
