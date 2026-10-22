"""Schemas for the canonical chat endpoint (POST /api/v1/chat).

Response contract (what the website widget and the Telegram bot already code
against)::

    {"answer": str, "citations": [{"n": 1, "title": str, "url": str|null,
     "source": "rag"|"web"}], "provider": str, "model": str|null,
     "fallback_used": bool, "refused": bool, "latency_ms": int}

``message`` is a byte-identical alias of ``answer`` for the legacy widget build
and older bot handler — both names are always populated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domains.chat.sanitize import strip_control

MAX_MESSAGE_CHARS = 1500

Persona = Literal["desk", "security", "news"]
Channel = Literal["web", "telegram"]


class ChatRequest(BaseModel):
    """Inbound question. ``channel`` marks the transport, not the behaviour."""

    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    persona: Persona = "desk"
    channel: Channel = "web"
    conversation_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = strip_control(value, MAX_MESSAGE_CHARS).strip()
        if not cleaned:
            raise ValueError("message is empty after sanitization")
        return cleaned


class Citation(BaseModel):
    """One numbered passage backing the answer — ``n`` matches ``[n]`` in text."""

    n: int
    title: str
    url: str | None = None
    source: str = "rag"
    library_id: str | None = None
    score: float | None = None


class ChatResponse(BaseModel):
    """The canonical answer envelope. Never a 5xx — degradation is a 200."""

    answer: str
    message: str = ""
    citations: list[Citation] = Field(default_factory=list)
    provider: str
    model: str | None = None
    fallback_used: bool = False
    refused: bool = False
    refusal_reason: str | None = None
    latency_ms: int

    @model_validator(mode="after")
    def _mirror_answer(self) -> ChatResponse:
        """Keep ``message`` identical to ``answer`` for legacy clients."""
        if not self.message:
            self.message = self.answer
        elif not self.answer:
            self.answer = self.message
        return self
