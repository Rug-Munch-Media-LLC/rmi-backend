"""Compatibility shim — the chat models now live in ``models.py``.

Kept so any importer of ``app.routers.chat._chat_models`` keeps working
against the SAME objects.
"""

from .models import (  # noqa: F401  (re-export for existing importers)
    _CHAT_MAX_TOKENS_BASE,
    _CHAT_MAX_TOKENS_MAX,
    _CHAT_MAX_TOKENS_MIN,
    CHAT_MAX_TOKENS_DEFAULT,
    ChatRequest,
    StreamRequest,
    _resolve_chat_max_tokens_default,
)
