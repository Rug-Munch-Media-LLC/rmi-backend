"""Orchestrator Agent — routes queries to scanner, RAG, or market specialists.

Two entry points:

* ``orchestrator_agent`` — Pydantic AI agent whose tools dispatch to the
  specialist agents (LLM-driven routing, emits ``OrchestratorResult``).
* ``AgentOrchestrator`` — programmatic orchestrator with deterministic
  keyword routing plus a unified, typed ``AgentResponse``. This is what the
  API endpoint uses: fast, predictable, and easy to test.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.agents.config import DEFAULT_CHAIN, get_agent_model
from app.agents.market import market_agent
from app.agents.models import AgentResponse, OrchestratorResult
from app.agents.rag import rag_agent
from app.agents.scanner import scanner_agent

# ── LLM-driven orchestrator agent ─────────────────────────────────────
orchestrator_agent = Agent(
    get_agent_model(),
    output_type=OrchestratorResult,
    system_prompt=(
        "You are the RugMunch orchestrator. Classify the user's query and "
        "dispatch to exactly one specialist tool: dispatch_scanner() for "
        "token/contract safety questions, dispatch_rag() for knowledge and "
        "exploit questions, dispatch_market() for market sentiment "
        "questions. After the specialist responds, emit OrchestratorResult "
        "with the chosen agent and a concise summary of its answer."
    ),
    model_settings=ModelSettings(temperature=0.2),
)


@orchestrator_agent.tool_plain
async def dispatch_scanner(query: str, chain: str = DEFAULT_CHAIN) -> dict[str, Any]:
    """Dispatch a token-safety query to the scanner specialist agent.

    Args:
        query: The user's token-risk question.
        chain: Blockchain name for the scan (e.g. "ethereum", "solana").
    """
    result = await scanner_agent.run(f"Chain: {chain}. {query}")
    return {"agent": "scanner", "data": result.output.model_dump()}


@orchestrator_agent.tool_plain
async def dispatch_rag(query: str) -> dict[str, Any]:
    """Dispatch a knowledge question to the RAG specialist agent.

    Args:
        query: The user's knowledge/exploit question.
    """
    result = await rag_agent.run(query)
    return {"agent": "rag", "data": result.output.model_dump()}


@orchestrator_agent.tool_plain
async def dispatch_market(query: str) -> dict[str, Any]:
    """Dispatch a market question to the market specialist agent.

    Args:
        query: The user's market sentiment question.
    """
    result = await market_agent.run(query)
    return {"agent": "market", "data": result.output.model_dump()}


# ── Programmatic orchestrator (used by the API) ───────────────────────
_SCANNER_KEYWORDS: tuple[str, ...] = (
    "scan",
    "honeypot",
    "rugpull",
    "rug pull",
    "token safe",
    "is safe",
    "contract audit",
    "scam",
)
_MARKET_KEYWORDS: tuple[str, ...] = (
    "market",
    "sentiment",
    "trend",
    "fear",
    "greed",
    "mover",
    "price",
    "bullish",
    "bearish",
    "pump",
)


def _summarize(target: str, data: Any) -> str:
    """Build a one-line summary from a typed specialist output."""
    if target == "scanner":
        return str(f"Risk score {data.risk_score}/100 — recommendation: {data.recommendation}")
    if target == "rag":
        return str(data.answer[:200])
    return str(data.summary[:200])


class AgentOrchestrator:
    """Deterministic router that dispatches to specialists and returns typed output.

    Keyword routing is intentionally simple and local: the 9B model is slow,
    so the API path avoids an LLM round-trip just to pick a specialist.
    """

    def __init__(self, specialists: dict[str, Any] | None = None) -> None:
        self.specialists: dict[str, Any] = specialists or {
            "scanner": scanner_agent,
            "rag": rag_agent,
            "market": market_agent,
        }

    def route(self, query: str) -> str:
        """Pick a specialist by keyword heuristics.

        Returns one of "scanner", "rag", "market" (defaults to "rag").
        """
        q = query.lower()
        if re.search(r"0x[a-f0-9]{6,}", q) or any(k in q for k in _SCANNER_KEYWORDS):
            return "scanner"
        if any(k in q for k in _MARKET_KEYWORDS):
            return "market"
        return "rag"

    async def run(self, query: str, agent: str = "auto", chain: str = DEFAULT_CHAIN) -> AgentResponse:
        """Run a query through the selected specialist and return a typed response.

        Args:
            query: User query.
            agent: "auto" to route by heuristics, or force "scanner"/"rag"/"market".
            chain: Blockchain hint passed to scanner queries.
        """
        target = agent if agent in ("scanner", "rag", "market") else self.route(query)
        specialist = self.specialists[target]
        if target == "scanner":
            result = await specialist.run(f"Chain: {chain}. {query}")
        else:
            result = await specialist.run(query)
        data = result.output
        return AgentResponse(
            query=query,
            agent=target,
            data=data,
            summary=_summarize(target, data),
        )
