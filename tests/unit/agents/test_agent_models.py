"""Tests for the Pydantic AI agent fleet models (app/agents/models.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.models import (
    AgentQuery,
    AgentResponse,
    KnowledgeResult,
    MarketResult,
    TokenRiskResult,
)


class TestTokenRiskResult:
    """TokenRiskResult validation and bounds."""

    def test_valid_minimal(self) -> None:
        result = TokenRiskResult(
            risk_score=42,
            risk_level="medium",
            is_honeypot=False,
            is_rugpull=True,
            warnings=["liquidity not locked"],
            deployer_risk="new deployer",
            liquidity_analysis="LP removed",
            recommendation="caution",
        )
        assert result.risk_score == 42
        assert result.recommendation == "caution"

    def test_list_fields_default_to_empty(self) -> None:
        result = TokenRiskResult(
            risk_score=10,
            risk_level="low",
            is_honeypot=False,
            is_rugpull=False,
            deployer_risk="clean",
            liquidity_analysis="locked 1y",
            recommendation="safe",
        )
        assert result.warnings == []

    def test_risk_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            TokenRiskResult(
                risk_score=101,
                risk_level="low",
                is_honeypot=False,
                is_rugpull=False,
                deployer_risk="clean",
                liquidity_analysis="locked",
                recommendation="safe",
            )
        with pytest.raises(ValidationError):
            TokenRiskResult(
                risk_score=-1,
                risk_level="low",
                is_honeypot=False,
                is_rugpull=False,
                deployer_risk="clean",
                liquidity_analysis="locked",
                recommendation="safe",
            )


class TestKnowledgeResult:
    """KnowledgeResult validation."""

    def test_valid(self) -> None:
        result = KnowledgeResult(
            answer="Reentrancy lets an attacker re-enter a contract before state updates.",
            sources=["scam_intel/2026-0001"],
            confidence=0.9,
            related_exploits=["TheDAO"],
        )
        assert result.confidence == 0.9
        assert result.sources[0].startswith("scam_intel")

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeResult(answer="x", sources=[], confidence=1.5, related_exploits=[])


class TestMarketResult:
    """MarketResult validation."""

    def test_valid(self) -> None:
        result = MarketResult(
            fear_index=25,
            market_sentiment="Fear",
            trending_tokens=["PEPE", "WIF"],
            recent_exploits=["0x... drained $2M"],
            top_movers_24h=[{"symbol": "DOGE", "change_pct": 12.4}],
            summary="Markets are fearful.",
        )
        assert result.fear_index == 25
        assert result.top_movers_24h[0]["symbol"] == "DOGE"

    def test_fear_index_bounds(self) -> None:
        with pytest.raises(ValidationError):
            MarketResult(
                fear_index=101,
                market_sentiment="Extreme Greed",
                summary="x",
            )


class TestAgentQuery:
    """API request model validation."""

    def test_defaults(self) -> None:
        query = AgentQuery(query="Is 0xabc safe?")
        assert query.agent == "auto"
        assert query.chain == "ethereum"

    def test_forced_agent(self) -> None:
        query = AgentQuery(query="market?", agent="market")
        assert query.agent == "market"

    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentQuery(query="")


class TestAgentResponse:
    """Unified response model wraps each typed specialist output."""

    def test_wraps_scanner_result(self) -> None:
        data = TokenRiskResult(
            risk_score=90,
            risk_level="high",
            is_honeypot=True,
            is_rugpull=True,
            warnings=["honeypot"],
            deployer_risk="anon",
            liquidity_analysis="0% locked",
            recommendation="avoid",
        )
        response = AgentResponse(query="q", agent="scanner", data=data, summary="avoid")
        assert response.data.risk_score == 90
        assert isinstance(response.data, TokenRiskResult)

    def test_wraps_rag_result(self) -> None:
        data = KnowledgeResult(answer="answer", sources=[], confidence=0.5, related_exploits=[])
        response = AgentResponse(query="q", agent="rag", data=data)
        assert isinstance(response.data, KnowledgeResult)
        assert response.summary is None
