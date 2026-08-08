"""#9 - Agent Memory Layer. Stores conversation history in Memgraph for long-term agent memory.
Enables agents to remember past interactions across sessions."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter

MEMGRAPH_URI = os.getenv("MEMGRAPH_URI", "bolt://localhost:7687")
MEMGRAPH_USER = os.getenv("MEMGRAPH_USER", "")
MEMGRAPH_PASS = os.getenv("MEMGRAPH_PASSWORD", "")

router = APIRouter(prefix="/api/v1/memory", tags=["agent-memory"])


async def _run_query(query: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Run a Cypher query against Memgraph."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "http://localhost:7444/db/memgraph/query",
                json={"query": query, "parameters": params or {}},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return r.json().get("data", [])
    except httpx.HTTPError:
        pass
    return []


@router.post("/conversation")
async def store_conversation(user_id: str, agent_id: str, message: str, role: str = "user") -> bool:
    """Store a conversation message in agent memory graph."""
    query = """
    MERGE (u:User {id: $user_id})
    MERGE (a:Agent {id: $agent_id})
    MERGE (c:Conversation {id: $conv_id})
    ON CREATE SET c.created_at = $timestamp
    CREATE (m:Message {
        role: $role,
        content: $message,
        timestamp: $timestamp
    })
    MERGE (u)-[:PARTICIPATES_IN]->(c)
    MERGE (a)-[:PARTICIPATES_IN]->(c)
    MERGE (c)-[:HAS_MESSAGE]->(m)
    MERGE (m)-[:SENT_BY]->(CASE WHEN $role = 'user' THEN u ELSE a END)
    """
    conv_id = f"{user_id}:{agent_id}"
    await _run_query(
        query,
        {
            "user_id": user_id,
            "agent_id": agent_id,
            "conv_id": conv_id,
            "role": role,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    return {"stored": True, "conversation_id": conv_id}


@router.get("/conversation/{user_id}/{agent_id}")
async def get_conversation(user_id: str, agent_id: str, limit: int = 20) -> dict[str, Any]:
    """Retrieve conversation history for an agent."""
    query = """
    MATCH (u:User {id: $user_id})-[:PARTICIPATES_IN]->(c:Conversation)<-[:PARTICIPATES_IN]-(a:Agent {id: $agent_id})
    MATCH (c)-[:HAS_MESSAGE]->(m:Message)
    RETURN m.role as role, m.content as content, m.timestamp as timestamp
    ORDER BY m.timestamp DESC LIMIT $limit
    """
    rows = await _run_query(query, {"user_id": user_id, "agent_id": agent_id, "limit": limit})
    return {
        "user_id": user_id,
        "agent_id": agent_id,
        "messages": [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)] if rows else [],
        "count": len(rows) if rows else 0,
    }


@router.get("/context/{user_id}")
async def get_user_context(user_id: str) -> dict[str, Any]:
    """Get all agent conversations + preferences for a user."""
    query = """
    MATCH (u:User {id: $user_id})-[:PARTICIPATES_IN]->(c:Conversation)
    MATCH (a:Agent)-[:PARTICIPATES_IN]->(c)
    RETURN a.id as agent, count(*) as messages
    ORDER BY messages DESC
    """
    rows = await _run_query(query, {"user_id": user_id})
    return {
        "user_id": user_id,
        "agents": [{"agent": r[0], "messages": r[1]} for r in rows] if rows else [],
    }
