"""Schemas for the canonical chat endpoint (POST /api/v1/chat)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
    """One numbered passage backing the answer."""

    index: int
    title: str
    url: str | None = None
    library_id: str | None = None
    source: str = "rag"
    score: float | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    provider: str
    fallback_used: bool = False
    latency_ms: int
    refused: bool = False
    refusal_reason: str | None = None
