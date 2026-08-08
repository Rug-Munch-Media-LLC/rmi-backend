"""
Intelligence Router - Models
=============================
Pydantic models for intelligence data.
"""

from __future__ import annotations
from typing import Any

from pydantic import BaseModel


class MarketUpdate(BaseModel):
    """Market intelligence update from n8n."""

    workflow: str
    data: dict


class IntelligenceQuery(BaseModel):
    """Query parameters for intelligence data."""

    chain: str | None = None
    min_amount_usd: float | None = None
    min_confidence: float | None = None
    limit: int = 50
    offset: int = 0
