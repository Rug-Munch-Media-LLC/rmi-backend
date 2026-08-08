"""Live tests for the Pydantic AI agent fleet.

Each test runs a real agent against the local llama.cpp model (DeepSeek-8B
at :8093) and asserts the typed output schema.

Marked ``integration`` — excluded from the default CI run
(``pytest -m "not integration"``) because they require the live model and
each run takes ~30-60s.

Note: pydantic-ai v2 returns the typed output on ``result.output`` (the
pre-v2 ``result.data`` attribute was removed).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

from app.agents import (  # noqa: E402 — module docstring precedes import block
    AgentOrchestrator,
    KnowledgeResult,
    MarketResult,
    TokenRiskResult,
    market_agent,
    orchestrator_agent,
    rag_agent,
    scanner_agent,
)


async def test_scanner_agent() -> None:
    """Scanner agent returns a typed TokenRiskResult for a token query."""
    result = await scanner_agent.run("Is 0x28c6c06209d25Cb12B5d0d3a2B94594d0c2a2592 a safe token?")
    assert isinstance(result.output, TokenRiskResult)
    assert 0 <= result.output.risk_score <= 100
    assert result.output.risk_level in ("low", "medium", "high", "critical")
    assert result.output.recommendation in ("safe", "caution", "avoid")


async def test_rag_agent() -> None:
    """RAG agent returns a typed KnowledgeResult for a knowledge query."""
    result = await rag_agent.run("What is a reentrancy attack?")
    assert isinstance(result.output, KnowledgeResult)
    assert result.output.answer
    assert 0 <= result.output.confidence <= 1


async def test_market_agent() -> None:
    """Market agent returns a typed MarketResult for a sentiment query."""
    result = await market_agent.run("Summarize current crypto market sentiment")
    assert isinstance(result.output, MarketResult)
    assert 0 <= result.output.fear_index <= 100
    assert result.output.summary


async def test_orchestrator_agent() -> None:
    """LLM-driven orchestrator dispatches and returns a unified result."""
    result = await orchestrator_agent.run("Is 0x28c6c06209d25Cb12B5d0d3a2B94594d0c2a2592 a safe token?")
    assert result.output.agent in ("scanner", "rag", "market")
    assert result.output.summary


async def test_orchestrator_programmatic_route() -> None:
    """Programmatic orchestrator returns a unified typed AgentResponse."""
    orch = AgentOrchestrator()
    response = await orch.run("Is 0x28c6c06209d25Cb12B5d0d3a2B94594d0c2a2592 a safe token?")
    assert response.agent == "scanner"
    assert isinstance(response.data, TokenRiskResult)
