"""Pydantic v2 models for the wallet domain.

No FastAPI imports. Pure data shapes.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(StrEnum):
    """Wallet risk classification."""

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


class Wallet(BaseModel):
    """A wallet identity (address + chain)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str = Field(..., min_length=1, max_length=256)
    chain: str = Field(default="solana", description="solana, ethereum, bsc, base, ...")
    label: str | None = Field(default=None, description="Human-readable label, e.g. 'Binance Hot Wallet'")


class TokenHolding(BaseModel):
    """A single token holding within a wallet's portfolio."""

    symbol: str
    address: str = ""
    amount: float = 0.0
    price_usd: float = 0.0
    value_usd: float = 0.0


class Transaction(BaseModel):
    """A wallet transaction summary."""

    hash: str
    type: str = "transfer"
    amount_usd: float = 0.0
    timestamp: int = 0
    block_time: datetime | None = None
    direction: str | None = Field(default=None, description="in | out | self")


class Balance(BaseModel):
    """Wallet balance + token holdings."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str
    chain: str
    native_balance: float = 0.0
    native_symbol: str = ""
    total_value_usd: float = 0.0
    tokens: list[TokenHolding] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class ScanFlag(BaseModel):
    """A risk flag raised by a scan."""

    code: str
    severity: RiskLevel
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class WalletAnalysis(BaseModel):
    """Full wallet analysis - risk + tokens + recent activity."""

    address: str
    chain: str
    truncated_address: str
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_level: RiskLevel
    tokens: list[TokenHolding] = Field(default_factory=list)
    recent_transactions: list[Transaction] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list, description="Known labels (e.g. 'Binance', 'Vitalik')")
    flags: list[ScanFlag] = Field(default_factory=list)
    total_value_usd: float = 0.0
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)


class ScanRequest(BaseModel):
    """Request body for the wallet scan endpoint."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str = Field(..., min_length=1, max_length=256)
    chain: str = Field(default="solana")
    tier: str = Field(default="free", description="free | pro | elite | internal")

    @field_validator("chain")
    @classmethod
    def _chain_lowercase(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("tier")
    @classmethod
    def _tier_lowercase(cls, v: str) -> str:
        return v.lower().strip()


class ScanResult(BaseModel):
    """Threat scan result."""

    address: str
    chain: str
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_level: RiskLevel
    flags: list[ScanFlag] = Field(default_factory=list)
    modules_run: list[str] = Field(default_factory=list)
    scanned_at: datetime = Field(default_factory=datetime.utcnow)
