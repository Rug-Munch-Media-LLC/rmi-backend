"""
RMI Database Client - Pydantic Models
======================================
Data models for Supabase-backed persistence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class User(BaseModel):
    id: str
    email: str | None = None
    wallet_address: str | None = None
    role: str = "USER"
    tier: str = "FREE"
    created_at: str | None = None
    xp: int = 0
    level: int = 1


class InvestigationCase(BaseModel):
    id: str
    target: str
    type: str = "wallet"
    status: str = "open"
    evidence: list[str] = Field(default_factory=list)
    agents_assigned: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    risk_score: float = 0.0
    findings: dict[str, Any] = Field(default_factory=dict)


class WalletScan(BaseModel):
    id: str | None = None
    address: str
    chain: str = "solana"
    risk_score: float = 0.0
    findings: dict[str, Any] = Field(default_factory=dict)
    scanned_at: str | None = None
    user_id: str | None = None


class ContractAudit(BaseModel):
    id: str | None = None
    address: str
    chain: str = "solana"
    risk_score: float = 0.0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    ai_analysis: dict[str, Any] = Field(default_factory=dict)
    audited_at: str | None = None


class TrenchesPost(BaseModel):
    id: str | None = None
    title: str
    content: str
    category: str = "discussion"
    author_id: str | None = None
    upvotes: int = 0
    comments: int = 0
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None


class Alert(BaseModel):
    id: str | None = None
    token_address: str
    types: list[str] = Field(default_factory=list)
    webhook_url: str | None = None
    active: bool = True
    created_at: str | None = None


class GamificationProfile(BaseModel):
    user_id: str
    xp: int = 0
    level: int = 1
    badges: list[str] = Field(default_factory=list)
    scans_count: int = 0
    posts_count: int = 0
    updated_at: str | None = None
