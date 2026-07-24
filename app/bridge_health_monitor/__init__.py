"""
Cross-Chain Bridge Health & Exploit Monitor
=============================================

Split from monolithic bridge_health_monitor.py into:
  - types.py     Enums & dataclasses
  - registry.py  Bridge registry (12 bridges)
  - monitor.py   BridgeHealthMonitor class
  - cli.py       Standalone CLI entry point
"""

from app.bridge_health_monitor.types import (
    BridgeHealthReport,
    BridgeSecurityScore,
    BridgeTVLSnapshot,
    ExploitSignal,
    RiskTier,
    TrustModel,
)
from app.bridge_health_monitor.registry import BRIDGE_REGISTRY
from app.bridge_health_monitor.monitor import BridgeHealthMonitor
from app.bridge_health_monitor.cli import main

__all__ = [
    "RiskTier",
    "TrustModel",
    "BridgeTVLSnapshot",
    "BridgeSecurityScore",
    "ExploitSignal",
    "BridgeHealthReport",
    "BRIDGE_REGISTRY",
    "BridgeHealthMonitor",
    "main",
]
