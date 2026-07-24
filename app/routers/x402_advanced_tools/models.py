"""
Request models for x402 advanced tool endpoints.
"""

from __future__ import annotations
from pydantic import BaseModel, Field

class AddressRequest(BaseModel):
    """Wallet or token address request."""

    address: str = Field(..., description="Wallet or token address")
    chain: str = Field(default="base")

class HistoryRequest(BaseModel):
    """Historical data request with lookback window."""

    address: str = Field(..., description="Token or wallet address")
    chain: str = Field(default="base")
    hours: int = Field(default=24, ge=1, le=168, description="Lookback window in hours (max 168)")

class NarrativeRequest(BaseModel):
    """Narrative/sentiment analysis request."""

    token: str = Field(..., description="Token symbol or address")
    chain: str = Field(default="all")

__all__ = ["AddressRequest", "HistoryRequest", "NarrativeRequest"]
