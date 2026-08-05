"""Shared Pydantic models for the Pydantic AI agent fleet.

Every specialist agent emits one of the typed result models below
(``TokenRiskResult``, ``KnowledgeResult``, ``MarketResult``). The API layer
serializes ``AgentResponse``, which wraps the specialist output plus routing
metadata.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TokenRiskResult(BaseModel):
    """Structured token risk assessment from the Scanner Agent."""

    risk_score: int = Field(ge=0, le=100, description="Risk score, 0=clean 100=definitely a rug")
    risk_level: str = Field(description='One of "low", "medium", "high", "critical"')
    is_honeypot: bool = Field(description="True when the token can be bought but not sold")
    is_rugpull: bool = Field(description="True when the project shows rug-pull indicators")
    warnings: list[str] = Field(default_factory=list, description="Human-readable risk warnings")
    deployer_risk: str = Field(description="Deployer reputation assessment")
    liquidity_analysis: str = Field(description="Liquidity lock/burn/removal analysis")
    recommendation: str = Field(description='One of "safe", "caution", "avoid"')


class KnowledgeResult(BaseModel):
    """Structured knowledge-grounded answer from the RAG Agent."""

    answer: str = Field(description="Direct answer to the user query")
    sources: list[str] = Field(default_factory=list, description="RAG source identifiers")
    confidence: float = Field(ge=0, le=1, description="Model confidence in the answer")
    related_exploits: list[str] = Field(default_factory=list, description="Related known exploit patterns")


class MarketResult(BaseModel):
    """Structured market snapshot from the Market Agent."""

    fear_index: int = Field(ge=0, le=100, description="Fear & Greed index, 0=fear 100=greed")
    market_sentiment: str = Field(description="Overall market sentiment label")
    trending_tokens: list[str] = Field(default_factory=list, description="Trending token symbols")
    recent_exploits: list[str] = Field(default_factory=list, description="Recent exploit headlines")
    top_movers_24h: list[dict[str, Any]] = Field(
        default_factory=list, description="Top 24h movers, e.g. [{symbol, change_pct}]"
    )
    summary: str = Field(description="One-paragraph market summary")


class OrchestratorResult(BaseModel):
    """Unified result emitted by the LLM-driven orchestrator agent."""

    agent: Literal["scanner", "rag", "market"] = Field(description="Specialist agent that handled the query")
    summary: str = Field(description="Concise answer synthesized by the orchestrator")


class AgentQuery(BaseModel):
    """Request body for ``POST /api/v1/agent/analyze``."""

    query: str = Field(min_length=1, max_length=2000, description="User query for the agent fleet")
    agent: Literal["auto", "scanner", "rag", "market"] = Field(
        default="auto", description="Force a specialist, or auto-route"
    )
    chain: str = Field(default="ethereum", description="Blockchain for scanner queries")


class AgentResponse(BaseModel):
    """Unified typed response from the agent fleet."""

    query: str = Field(description="Original user query")
    agent: Literal["scanner", "rag", "market"] = Field(description="Specialist that answered")
    data: TokenRiskResult | KnowledgeResult | MarketResult = Field(description="Typed specialist output")
    summary: str | None = Field(default=None, description="One-line summary of the result")
