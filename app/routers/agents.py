"""
Agents Router — Agent Mesh listing, detail, commands
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["agents"])

# ── Agent definitions ──
AGENTS = {
    "nexus": {
        "name": "NEXUS",
        "role": "Strategic Coordinator",
        "tier": "T0",
        "models": ["gemini-2.5-pro", "nvidia/nemotron-4-340b"],
        "triggers": ["strategize", "plan", "coordinate", "synthesize"],
    },
    "scout": {
        "name": "SCOUT",
        "role": "Alpha Hunter",
        "tier": "T3",
        "models": ["groq/llama-3.1-8b-instant", "gemini-2.5-flash"],
        "triggers": ["find", "scan", "hunt", "alpha"],
    },
    "tracer": {
        "name": "TRACER",
        "role": "Forensic Investigator",
        "tier": "T1",
        "models": ["gemini-2.5-pro", "deepseek/deepseek-r1"],
        "triggers": ["trace", "investigate", "follow", "wallet"],
    },
    "cipher": {
        "name": "CIPHER",
        "role": "Contract Auditor",
        "tier": "T1",
        "models": ["qwen/qwen2.5-coder-32b-instruct", "deepseek/deepseek-coder-v2"],
        "triggers": ["audit", "security", "contract", "code"],
    },
    "sentinel": {
        "name": "SENTINEL",
        "role": "Rug Detector",
        "tier": "T2",
        "models": ["deepseek/deepseek-r1", "groq/llama-3.3-70b-versatile"],
        "triggers": ["monitor", "watch", "alert", "rug"],
    },
    "chronicler": {
        "name": "CHRONICLER",
        "role": "Investigative Reporter",
        "tier": "T2",
        "models": ["deepseek/deepseek-r1", "gemini-2.5-flash"],
        "triggers": ["write", "document", "report", "evidence"],
    },
    "forge": {
        "name": "FORGE",
        "role": "Implementation Architect",
        "tier": "T1",
        "models": ["qwen/qwen2.5-coder-32b-instruct", "deepseek/deepseek-coder-v2"],
        "triggers": ["code", "implement", "build", "script"],
    },
    "relay": {
        "name": "RELAY",
        "role": "Communications Coordinator",
        "tier": "T3",
        "models": ["groq/llama-3.1-8b-instant", "gemini-2.5-flash"],
        "triggers": ["format", "relay", "dispatch", "notify"],
    },
}


class AgentCommandRequest(BaseModel):
    agent: str = Field(
        ...,
        description="Agent name: nexus, scout, tracer, cipher, sentinel, chronicler, forge, relay",
    )
    command: str
    context: dict | None = None
    priority: str = Field(default="normal")


from app.auth import get_redis


# ── Static routes must comes before parameterized routes to avoid FastAPI matching issues ──
@router.get("/agents/specter")
async def specter_status():
    """SPECTER agent status and capabilities"""
    import os

    return {
        "agent": "SPECTER",
        "emoji": "👻",
        "role": "OSINT & Social Forensics",
        "provider": "Together AI",
        "models": [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "mistralai/Mixtral-8x22B-Instruct-v0.1",
        ],
        "free_credits": "$5",
        "capabilities": [
            "brave_web_search",
            "website_forensics",
            "firecrawl_scraping",
            "dev_identity_hunting",
            "social_media_analysis",
            "litepaper_plagiarism_detection",
            "sockpuppet_detection",
        ],
        "integrations": {
            "brave_search": bool(os.getenv("BRAVE_API_KEY")),
            "firecrawl": bool(os.getenv("FIRECRAWL_API_KEY")),
            "apify": bool(os.getenv("APIFY_API_KEY")),
            "together_ai": bool(os.getenv("TOGETHER_API_KEY")),
        },
        "status": "online",
    }


@router.get("/agents")
async def list_agents():
    return {"agents": AGENTS, "total": len(AGENTS)}


# ── Helper to get Redis synchronously ──
def _get_redis_sync():
    """Get Redis instance synchronously (get_redis returns redis.Redis, not async)."""
    return get_redis()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    if agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    r = _get_redis_sync()
    status_raw = r.hget("rmi:agents", agent_id)
    status = json.loads(status_raw) if status_raw else {"status": "online", "last_ping": datetime.utcnow().isoformat()}
    return {**AGENTS[agent_id], **status}


@router.post("/agents/{agent_id}/command")
async def agent_command(agent_id: str, req: AgentCommandRequest):
    if agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    agent = AGENTS[agent_id]
    r = _get_redis_sync()

    task_id = f"task:{datetime.utcnow().timestamp():.0f}:{agent_id}"
    task_data = {
        "id": task_id,
        "agent": agent_id,
        "command": req.command,
        "context": req.context or {},
        "priority": req.priority,
        "status": "queued",
        "created": datetime.utcnow().isoformat(),
    }
    r.hset("rmi:tasks", task_id, json.dumps(task_data))
    r.lpush("rmi:queue:" + req.priority, task_id)

    return {"task_id": task_id, "agent": agent["name"], "status": "queued", "command": req.command}
