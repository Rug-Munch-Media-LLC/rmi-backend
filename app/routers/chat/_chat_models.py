"""Chat request/response models + max-token budget resolution.

Split out of ``app/routers/chat/__init__.py`` (de-godding phase 2,
behavior-preserving). Re-exported from the package root so every existing
importer keeps working against the SAME objects.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

# ── Models ──
# Chat answers are short prose — a 320-token budget keeps generation latency
# snappy on the free remote chat tier (measured 2026-08-15 on the old CPU
# host: generation dominated total latency, so the token budget IS the
# latency lever). The default can be raised at deploy time via
# RMI_CHAT_MAX_TOKENS (clamped to 64..2048 — the Pydantic Field bounds stay
# 64..8192 for explicit requests).
# 2026-08-14: raised 512 -> 1536 — 512 was truncating long educational answers
# (e.g. "how to learn TA") mid-sentence for the bot, which sends no max_tokens.
# 2026-08-15: 1536 -> 768 — context fetches (tool/RAG/web) now run in parallel
# via asyncio.gather and the tool-result block is capped at 1200 chars, so the
# prompt stays lean while the model gets fresher grounding; 768 keeps long
# educational answers from truncating while bounding generation latency.
# 2026-08-15: 768 -> 320 — the bot sends max_tokens explicitly (768) and the
# 4B local model needed 87-136s per answer on CPU, blowing past the client
# timeout and the bot's 60s backend timeout, forcing the slow fallback chain.
# 320 tokens ≈ 1300 chars — a full Telegram answer for most queries (long
# answers are chunked bot-side at 3800 chars). Long educational answers may
# truncate at 320; raise per-deploy via RMI_CHAT_MAX_TOKENS if needed.
_CHAT_MAX_TOKENS_BASE = 320
_CHAT_MAX_TOKENS_MIN = 64
_CHAT_MAX_TOKENS_MAX = 2048


def _resolve_chat_max_tokens_default(env_value: str | None = None) -> int:
    """Resolve the ChatRequest ``max_tokens`` default.

    Returns ``_CHAT_MAX_TOKENS_BASE`` (512) unless ``RMI_CHAT_MAX_TOKENS`` is
    set to a parseable int, in which case the value is clamped to
    ``64..2048``. Unparseable values fall back to the base default.
    """
    raw = os.getenv("RMI_CHAT_MAX_TOKENS") if env_value is None else env_value
    if raw is None or not raw.strip():
        return _CHAT_MAX_TOKENS_BASE
    try:
        parsed = int(raw)
    except ValueError:
        return _CHAT_MAX_TOKENS_BASE
    return max(_CHAT_MAX_TOKENS_MIN, min(parsed, _CHAT_MAX_TOKENS_MAX))


CHAT_MAX_TOKENS_DEFAULT = _resolve_chat_max_tokens_default()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    model: str | None = Field(default=None, description="Specific model to use")
    tier: str = Field(default="T2", pattern="^(T0|T1|T2|T3|T4)$", description="AI model tier")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=CHAT_MAX_TOKENS_DEFAULT, ge=64, le=8192)
    context_override: dict[str, Any] | None = Field(
        default=None, description="Custom context injection. `system_context` (str) is appended to the system prompt."
    )
    history: list[dict[str, str]] | None = Field(
        default=None,
        description=(
            "Explicit conversation history (user/assistant messages). "
            "When provided it OVERRIDES the DB-loaded history for this request "
            "(used by trusted server-side callers such as the Telegram bot, "
            "which must not read the shared anonymous history bucket)."
        ),
    )


class StreamRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    model: str | None = Field(default=None)
    tier: str = Field(default="T2", pattern="^(T0|T1|T2|T3|T4)$")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=64, le=8192)
