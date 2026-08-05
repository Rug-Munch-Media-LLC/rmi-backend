"""V1 agent analyze route — ``POST /api/v1/agent/analyze``.

Runs a user query through the orchestrator and returns a typed result from
the Pydantic AI agent fleet (scanner / rag / market).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.agents import AgentOrchestrator, AgentQuery, AgentResponse

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post("/analyze", response_model=AgentResponse)
async def analyze(req: AgentQuery) -> AgentResponse:
    """Route a query to the appropriate specialist agent and return typed output.

    Args:
        req: Query plus optional forced specialist and chain hint.

    Returns:
        AgentResponse wrapping the typed specialist result (TokenRiskResult,
        KnowledgeResult, or MarketResult) plus routing metadata.
    """
    orchestrator = AgentOrchestrator()
    return await orchestrator.run(req.query, agent=req.agent, chain=req.chain)
