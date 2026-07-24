"""Shared data types for Temporal workflows and activities."""

from __future__ import annotations
from dataclasses import dataclass

@dataclass
class ScanTaskParams:
    address: str
    chain: str = "ethereum"
    requester_id: str | None = None

@dataclass
class ScanResult:
    address: str
    chain: str
    risk_score: float | None
    risk_tier: str
    error: str | None = None
    completed: bool = False
