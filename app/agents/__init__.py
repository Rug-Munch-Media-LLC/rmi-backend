"""Pydantic AI agent fleet for the RugMunch platform.

Specialist agents with typed outputs, backed by the local Qwen3.5-9B model
via llama.cpp (OpenAI-compatible endpoint):

* ``scanner_agent``  — token risk analysis       → ``TokenRiskResult``
* ``rag_agent``      — knowledge/exploit answers → ``KnowledgeResult``
* ``market_agent``   — market snapshot           → ``MarketResult``
* ``orchestrator``   — routing + unified response

Legacy Instructor-based modules (base.py, specialists.py,
structured_outputs.py, tools.py) remain importable but are superseded by
the Pydantic AI agents defined here.
"""

from __future__ import annotations

from app.agents.market import market_agent
from app.agents.models import (
    AgentQuery,
    AgentResponse,
    KnowledgeResult,
    MarketResult,
    OrchestratorResult,
    TokenRiskResult,
)
from app.agents.orchestrator import AgentOrchestrator, orchestrator_agent
from app.agents.rag import rag_agent
from app.agents.scanner import scanner_agent

__all__ = [
    "AgentOrchestrator",
    "AgentQuery",
    "AgentResponse",
    "KnowledgeResult",
    "MarketResult",
    "OrchestratorResult",
    "TokenRiskResult",
    "market_agent",
    "orchestrator_agent",
    "rag_agent",
    "scanner_agent",
]
