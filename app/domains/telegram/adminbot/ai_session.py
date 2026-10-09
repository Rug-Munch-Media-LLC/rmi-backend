"""
AI session & OC confirmation state — extracted from admin_ai.py for structure gate.
"""

from __future__ import annotations

import time
from typing import Any

# Per-user state
_user_sessions: dict[int, dict[str, Any]] = {}

# Pending /oc (tooled) confirmations: user_id -> {prompt, task, ts}
_pending_oc: dict[int, dict[str, Any]] = {}

OC_CONFIRM_TTL = 300  # 5 minutes


def get_session(user_id: int) -> dict[str, Any]:
    """Get or create a user session."""
    if user_id not in _user_sessions:
        from app.domains.telegram.adminbot.admin_ai import DEFAULT_MODEL

        _user_sessions[user_id] = {
            "model": DEFAULT_MODEL,
            "history": [],
            "tokens": 0,
            "messages": 0,
        }
    return _user_sessions[user_id]


def set_model(user_id: int, model_id: str) -> None:
    from app.domains.telegram.adminbot.admin_ai import MODELS

    if model_id in MODELS:
        get_session(user_id)["model"] = model_id


def clear_history(user_id: int) -> None:
    s = get_session(user_id)
    s["history"] = []
    s["messages"] = 0


def get_usage(user_id: int) -> dict[str, Any]:
    s = get_session(user_id)
    return {"model": s["model"], "tokens": s["tokens"], "messages": s["messages"]}


# ════════════════════════════════════════════════
# TOOLED /OC CONFIRMATION GATE
# ════════════════════════════════════════════════


def stage_oc_prompt(user_id: int, prompt: str, task: bool = False) -> None:
    """Stage a tooled opencode prompt awaiting explicit confirmation."""
    _pending_oc[user_id] = {"prompt": prompt, "task": task, "ts": time.time()}


def pop_oc_prompt(user_id: int) -> dict[str, Any] | None:
    """Pop a pending /oc prompt if confirmed within TTL, else drop it."""
    entry = _pending_oc.pop(user_id, None)
    if entry is None:
        return None
    if time.time() - entry["ts"] > OC_CONFIRM_TTL:
        return None
    return entry


def cancel_oc_prompt(user_id: int) -> None:
    """Discard any pending /oc prompt."""
    _pending_oc.pop(user_id, None)
