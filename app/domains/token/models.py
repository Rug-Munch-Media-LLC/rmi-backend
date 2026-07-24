"""Pydantic v2 models for the token domain.

No FastAPI imports. Pure data shapes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(StrEnum):
    """Token risk classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: int) -> RiskLevel:
        if score < 20:
            return cls.LOW
        if score < 50:
            return cls.MEDIUM
        if score < 80:
            return cls.HIGH
        return cls.CRITICAL

class Token(BaseModel):
    """Token identity."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str = Field(..., min_length=1, max_length=256)
    chain: str = Field(default="solana")
    name: str = ""
    symbol: str = ""
    decimals: int = 9

class TokenDetail(BaseModel):
    """Full token details - metadata + supply + verification."""

    address: str
    chain: str
    name: str = ""
    symbol: str = ""
    decimals: int = 9
    total_supply: float = 0.0
    circulating_supply: float = 0.0
    holders: int = 0
    verified: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

class TokenHolder(BaseModel):
    """A token holder entry."""

    address: str
    balance: float = 0.0
    percentage: float = 0.0
    label: str | None = None
    is_contract: bool = False
    is_locked: bool = False

class TokenLiquidity(BaseModel):
    """Token liquidity summary."""

    address: str
    chain: str
    total_liquidity_usd: float = 0.0
    pools: list[dict[str, Any]] = Field(default_factory=list)
    locked_percentage: float = 0.0
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

class TokenRisk(BaseModel):
    """Token risk assessment."""

    address: str
    chain: str
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_level: RiskLevel
    flags: list[dict[str, Any]] = Field(default_factory=list)
    honeypot: bool = False
    can_sell: bool = True
    can_buy: bool = True
    take_back: bool = False
    owner_change_balance: bool = False
    transfer_pausable: bool = False
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)

class TokenScanRequest(BaseModel):
    """Request body for the token scan endpoint."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str = Field(..., min_length=1, max_length=256)
    chain: str = Field(default="solana")
    tier: str = Field(default="free")

    @field_validator("chain", "tier")
    @classmethod
    def _lowercase(cls, v: str) -> str:
        return v.lower().strip()

class TokenScanResult(BaseModel):
    """Full token scan result."""

    address: str
    chain: str
    safety_score: float = 100.0
    risk_score: int = 0
    risk_level: RiskLevel
    risk_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    modules_run: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    scanned_at: datetime = Field(default_factory=datetime.utcnow)
