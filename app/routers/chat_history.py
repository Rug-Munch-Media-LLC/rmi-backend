"""Chat history and stats endpoints.

Extracted from app/routers/chat.py (file-taming split). Mounted inside the
main chat router (prefix /api/v1/chat) via include_router — no mount.py
change needed.

Endpoints:
  GET    /api/v1/chat/history  — Get user's chat history
  GET    /api/v1/chat/stats    — Get user's chat statistics
  DELETE /api/v1/chat/history  — Clear user's chat history
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.auth import require_auth

from .chat_db import _get_db

router = APIRouter(tags=["chat"])


@router.get("/history")
async def chat_history(
    limit: int = 50,
    offset: int = 0,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get user's chat history."""
    user_id = user.get("id")

    conn = _get_db()
    rows = conn.execute(
        """SELECT id, role, content, metadata, created_at
           FROM chat_messages
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    ).fetchall()
    conn.close()

    messages = []
    for row in rows:
        messages.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
            }
        )

    return {"count": len(messages), "messages": list(reversed(messages))}


@router.get("/stats")
async def chat_stats(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """Get user's chat statistics."""
    user_id = user.get("id")

    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id = ?", (user_id,)).fetchone()[0]
    total_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM chat_messages").fetchone()[0]
    conn.close()

    return {
        "total_messages": total,
        "total_conversations": total_users,
    }


@router.delete("/history")
async def clear_chat_history(user: dict[str, Any] = Depends(require_auth)) -> dict[str, str]:
    """Clear user's chat history."""
    user_id = user.get("id")

    conn = _get_db()
    conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    return {"status": "ok", "message": "Chat history cleared"}
