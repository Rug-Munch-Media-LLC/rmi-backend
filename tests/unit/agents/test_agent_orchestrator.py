"""Tests for the agent orchestrator routing + unified response.

Uses fake specialist agents (no LLM, no network) injected via the
``AgentOrchestrator(specialists=...)`` constructor.
"""

from __future__ import annotations

from typing import Any

from app.agents.models import KnowledgeResult, MarketResult, TokenRiskResult
from app.agents.orchestrator import AgentOrchestrator


class FakeRunResult:
    """Mimics ``AgentRunResult`` — carries the typed ``.output``."""

    def __init__(self, output: Any) -> None:
        self.output = output


class FakeAgent:
    """Mimics a Pydantic AI agent: async ``run(query)`` returning typed output."""

    def __init__(self, output: Any) -> None:
        self._output = output
        self.calls: list[str] = []

    async def run(self, query: str) -> FakeRunResult:
        self.calls.append(query)
        return FakeRunResult(self._output)


def _scanner_output() -> TokenRiskResult:
    return TokenRiskResult(
        risk_score=95,
        risk_level="critical",
        is_honeypot=True,
        is_rugpull=True,
        warnings=["honeypot detected"],
        deployer_risk="anonymous",
        liquidity_analysis="LP removed",
        recommendation="avoid",
    )


def _rag_output() -> KnowledgeResult:
    return KnowledgeResult(
        answer="Reentrancy is an attack where a contract calls an external contract before updating its own state.",
        sources=["scam_intel/reentrancy"],
        confidence=0.85,
        related_exploits=["TheDAO"],
    )


def _market_output() -> MarketResult:
    return MarketResult(
        fear_index=30,
        market_sentiment="Fear",
        trending_tokens=["PEPE"],
        recent_exploits=[],
        top_movers_24h=[{"symbol": "DOGE", "change_pct": 5.2}],
        summary="Markets are fearful, majors down 2%.",
    )


class TestRouting:
    """Deterministic keyword routing."""

    def test_address_query_routes_to_scanner(self) -> None:
        assert AgentOrchestrator().route("Is 0x28c6c06209d25Cb12B5d0d3a2B94594d0c2a2592 safe?") == "scanner"

    def test_honeypot_query_routes_to_scanner(self) -> None:
        assert AgentOrchestrator().route("Check this token for honeypot") == "scanner"

    def test_market_query_routes_to_market(self) -> None:
        assert AgentOrchestrator().route("How is market sentiment today?") == "market"

    def test_general_knowledge_routes_to_rag(self) -> None:
        assert AgentOrchestrator().route("What is a reentrancy attack?") == "rag"

    def test_unknown_query_defaults_to_rag(self) -> None:
        assert AgentOrchestrator().route("tell me a joke") == "rag"


class TestOrchestratorRun:
    """Unified typed response from a specialist."""

    async def test_scanner_dispatch_returns_typed_response(self) -> None:
        fake = FakeAgent(_scanner_output())
        orch = AgentOrchestrator(specialists={"scanner": fake, "rag": fake, "market": fake})

        response = await orch.run("Is 0xabc a safe token?", agent="scanner")

        assert response.agent == "scanner"
        assert isinstance(response.data, TokenRiskResult)
        assert response.data.risk_score == 95
        assert response.summary == "Risk score 95/100 — recommendation: avoid"

    async def test_auto_route_dispatches_to_scanner(self) -> None:
        fake = FakeAgent(_scanner_output())
        orch = AgentOrchestrator(specialists={"scanner": fake, "rag": fake, "market": fake})

        response = await orch.run("Is 0x28c6c06209d25Cb12B5d0d3a2B94594d0c2a2592 a honeypot?")

        assert response.agent == "scanner"
        assert "Chain:" in fake.calls[0]  # chain hint forwarded to scanner prompt

    async def test_auto_route_dispatches_to_rag(self) -> None:
        fake = FakeAgent(_rag_output())
        orch = AgentOrchestrator(specialists={"scanner": fake, "rag": fake, "market": fake})

        response = await orch.run("What is a reentrancy attack?")

        assert response.agent == "rag"
        assert isinstance(response.data, KnowledgeResult)
        assert response.data.answer.startswith("Reentrancy")

    async def test_forced_market_ignores_routing(self) -> None:
        fake = FakeAgent(_market_output())
        orch = AgentOrchestrator(specialists={"scanner": fake, "rag": fake, "market": fake})

        response = await orch.run("tell me a joke", agent="market")

        assert response.agent == "market"
        assert isinstance(response.data, MarketResult)
        assert response.summary == "Markets are fearful, majors down 2%."
