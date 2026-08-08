"""Pydantic models for structured agent outputs using Instructor."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TokenRiskReport(BaseModel):
    """Structured token risk analysis output."""

    token_address: str = Field(description="Token contract address")
    risk_score: int = Field(ge=0, le=100, description="0=clean, 100=definitely a rug")
    risk_tier: RiskTier = Field(description="Risk classification tier")
    findings: list[str] = Field(description="Key risk findings")
    recommendation: str = Field(description="Action recommendation: buy/hold/sell/avoid")
    confidence: float = Field(ge=0, le=1, description="Model confidence in assessment")


class WalletProfile(BaseModel):
    """Structured wallet analysis output."""

    address: str
    entity_label: str | None = None
    activity_score: int = Field(ge=0, le=100)
    flags: list[str] = Field(default_factory=list)
    summary: str


class AuditReport(BaseModel):
    """Structured code audit output."""

    contract_name: str
    vulnerabilities: list[dict[str, Any]] = Field(description="List of {severity, description, line, fix}")
    overall_severity: RiskTier
    audit_summary: str
