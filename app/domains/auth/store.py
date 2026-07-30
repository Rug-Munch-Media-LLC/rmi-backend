"""Redis-backed user storage helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)


def _get_user(user_id: str) -> dict[str, Any] | None:
    """Fetch user record by id from Redis hash rmi:users."""
    r = get_redis()
    if not r:
        return None
    raw = r.hget("rmi:users", user_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_user_by_email(email: str) -> dict[str, Any] | None:
    """Fetch user record by email via rmi:users:email index."""
    r = get_redis()
    if not r:
        return None
    user_id = r.hget("rmi:users:email", email.lower())
    if not user_id:
        return None
    if isinstance(user_id, bytes):
        return _get_user(user_id.decode("utf-8"))
    return _get_user(user_id)


def _save_user(user: dict[str, Any]) -> None:
    """Persist user record to Redis hash rmi:users, refresh email index if present."""
    r = get_redis()
    if not r:
        return
    user_id = user["id"]
    r.hset("rmi:users", user_id, json.dumps(user))
    if user.get("email"):
        r.hset("rmi:users:email", user["email"].lower(), user_id)


def _is_valid_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None
