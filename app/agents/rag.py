"""RAG Agent — knowledge-grounded answers via the RugBrain search service.

Specialist agent built on Pydantic AI. Emits a typed ``KnowledgeResult`` by
consulting the RugBrain RAG index (``POST /api/v1/search`` on :8012).

The search tool degrades gracefully: if the RAG service is unreachable it
returns a structured error dict and the model answers from its own
knowledge, marking lower confidence.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.agents.config import (
    HTTP_TIMEOUT,
    RAG_API_KEY,
    RAG_API_URL,
    get_agent_model,
)
from app.agents.models import KnowledgeResult

rag_agent = Agent(
    get_agent_model(),
    output_type=KnowledgeResult,
    system_prompt=(
        "You are a crypto knowledge agent grounded in the RugBrain RAG "
        "index. For questions about exploits, vulnerabilities, and security "
        "patterns, call search_knowledge() to retrieve sources, then "
        "synthesize a KnowledgeResult with an answer, source identifiers, "
        "and a confidence score. If the search service is unavailable, "
        "answer from your own knowledge with a low confidence score."
    ),
    model_settings=ModelSettings(temperature=0.2),
)


async def _search_rag(query: str) -> dict[str, Any]:
    """Call the RugBrain RAG search service. Returns a dict, never raises."""
    headers: dict[str, str] = {}
    if RAG_API_KEY:
        headers["X-API-Key"] = RAG_API_KEY
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(RAG_API_URL, json={"query": query}, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
    except Exception as exc:  # tool must degrade gracefully
        return {"error": f"rag service unavailable: {exc}", "query": query}


@rag_agent.tool_plain
async def search_knowledge(query: str) -> dict[str, Any]:
    """Search RugBrain RAG for knowledge grounded in indexed security data.

    Args:
        query: Natural-language question about exploits or security patterns.
    """
    return await _search_rag(query)
