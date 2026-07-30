"""
Dify AI advisor proxy admin endpoints.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from . import verify_admin

logger = logging.getLogger(__name__)

dify_router = APIRouter()


def _brightdata_client(timeout: int = 30) -> Any:
    """Create an httpx client routed through Bright Data residential proxy."""
    import httpx

    proxy_url = os.getenv("BRIGHTDATA_PROXY_URL")
    if not proxy_url:
        raise RuntimeError("BRIGHTDATA_PROXY_URL environment variable is required")
    return httpx.AsyncClient(timeout=timeout, proxy=proxy_url, verify=True)


@dify_router.get("/dify/status")
async def dify_status(request: Request, _=Depends(verify_admin)) -> dict[str, str] | Any:
    """Check Dify AI advisor status."""
    dify_url = os.getenv("DIFY_URL", "")
    dify_key = os.getenv("DIFY_API_KEY", "")
    if not dify_url or not dify_key:
        return {
            "status": "not_configured",
            "note": "DIFY_URL or DIFY_API_KEY not set",
            "dify_url": dify_url or "",
        }
    try:
        client = _brightdata_client(10)
        async with client:
            resp = await client.get(
                f"{dify_url}/v1/parameters",
                headers={"Authorization": f"Bearer {dify_key}"},
            )
            if resp.status_code == 200:
                return {"status": "ok", "dify_url": dify_url}
            return {"status": "error", "code": resp.status_code, "dify_url": dify_url}
    except Exception as e:
        return {"status": "error", "error": str(e), "dify_url": dify_url}


@dify_router.get("/dify/conversations")
async def dify_conversations(
    request: Request, _=Depends(verify_admin), limit: int = Query(20, le=100)
) -> dict[str, list] | dict:
    """Get Dify conversation list."""
    dify_url = os.getenv("DIFY_URL", "")
    dify_key = os.getenv("DIFY_API_KEY", "")
    if not dify_url or not dify_key:
        return {"conversations": [], "error": "DIFY_URL or DIFY_API_KEY not set"}
    try:
        client = _brightdata_client(15)
        async with client:
            resp = await client.get(
                f"{dify_url}/v1/conversations",
                params={"limit": limit},
                headers={"Authorization": f"Bearer {dify_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"conversations": data.get("data", []), "total": data.get("total", 0)}
            return {"conversations": [], "error": f"Dify HTTP {resp.status_code}"}
    except Exception as e:
        return {"conversations": [], "error": str(e)}


@dify_router.get("/dify/apps")
async def dify_apps(
    request: Request, _=Depends(verify_admin), use_proxy: bool = False
) -> dict[str, list] | dict[str, Any | None] | Any:
    """Get Dify apps list."""
    dify_url = os.getenv("DIFY_URL", "")
    dify_key = os.getenv("DIFY_API_KEY", "")
    if not dify_url or not dify_key:
        return {"apps": [], "error": "DIFY_URL or DIFY_API_KEY not set"}
    try:
        client = _brightdata_client(15)
        async with client:
            resp = await client.get(
                f"{dify_url}/v1/apps",
                headers={"Authorization": f"Bearer {dify_key}"},
            )
            if resp.status_code == 200:
                return {"apps": resp.json().get("data", [])}
            return {"apps": [], "error": f"Dify HTTP {resp.status_code}"}
    except Exception as e:
        return {"apps": [], "error": str(e)}


@dify_router.get("/dify/conversations/{conversation_id}/messages")
async def dify_messages(
    request: Request, conversation_id: str, _=Depends(verify_admin), limit: int = Query(30, le=100)
) -> dict[str, list] | dict[str, str]:
    """Get messages for a Dify conversation."""
    dify_url = os.getenv("DIFY_URL", "")
    dify_key = os.getenv("DIFY_API_KEY", "")
    if not dify_url or not dify_key:
        return {"messages": [], "error": "DIFY_URL or DIFY_API_KEY not set"}
    try:
        client = _brightdata_client(15)
        async with client:
            resp = await client.get(
                f"{dify_url}/v1/messages",
                params={"conversation_id": conversation_id, "limit": limit},
                headers={"Authorization": f"Bearer {dify_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"messages": data.get("data", []), "total": data.get("total", 0)}
            return {"messages": [], "error": f"Dify HTTP {resp.status_code}"}
    except Exception as e:
        return {"messages": [], "error": str(e)}


class DifyChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None


@dify_router.post("/dify/chat")
async def dify_chat(
    request: Request, req: DifyChatRequest, _=Depends(verify_admin)
) -> dict[str, str] | dict[str, Any | None] | Any | dict:
    """Send a chat message to Dify AI advisor."""
    dify_url = os.getenv("DIFY_URL", "")
    dify_key = os.getenv("DIFY_API_KEY", "")
    if not dify_url or not dify_key:
        return {"error": "DIFY_URL or DIFY_API_KEY not set"}
    try:
        client = _brightdata_client(60)
        async with client:
            payload: dict[str, Any] = {"query": req.query, "response_mode": "blocking"}
            if req.conversation_id:
                payload["conversation_id"] = req.conversation_id
            resp = await client.post(
                f"{dify_url}/v1/chat-messages",
                headers={"Authorization": f"Bearer {dify_key}", "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "answer": data.get("answer", ""),
                    "conversation_id": data.get("conversation_id", ""),
                    "message_id": data.get("message_id", ""),
                }
            return {"error": f"Dify HTTP {resp.status_code}", "detail": resp.text[:300]}
    except Exception as e:
        return {"error": str(e)}
